import os
import json
import logging
from web3 import Web3
from decimal import Decimal
from config import config  # gas, deadline e slippage do ambiente

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- ABI mínimas para o Router ---
ROUTER_ABI = [
  {
    "inputs": [
      { "internalType": "uint256", "name": "amountIn", "type": "uint256" },
      { "internalType": "address[]", "name": "path", "type": "address[]" }
    ],
    "name": "getAmountsOut",
    "outputs": [
      { "internalType": "uint256[]", "name": "amounts", "type": "uint256[]" }
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "inputs": [
      { "internalType": "uint256", "name": "amountOutMin", "type": "uint256" },
      { "internalType": "address[]", "name": "path", "type": "address[]" },
      { "internalType": "address", "name": "to", "type": "address" },
      { "internalType": "uint256", "name": "deadline", "type": "uint256" }
    ],
    "name": "swapExactETHForTokens",
    "outputs": [
      { "internalType": "uint256[]", "name": "amounts", "type": "uint256[]" }
    ],
    "stateMutability": "payable",
    "type": "function"
  }
]

# --- ABI mínimas para Pairs/Pools ---
V2_PAIR_ABI = [
    {"name": "getReserves", "outputs": [
        {"type": "uint112", "name": "_reserve0"},
        {"type": "uint112", "name": "_reserve1"},
        {"type": "uint32", "name": "_blockTimestampLast"}
    ], "inputs": [], "stateMutability": "view", "type": "function"},
    {"name": "token0", "outputs": [{"type": "address", "name": ""}],
     "inputs": [], "stateMutability": "view", "type": "function"},
    {"name": "token1", "outputs": [{"type": "address", "name": ""}],
     "inputs": [], "stateMutability": "view", "type": "function"}
]

V3_POOL_ABI = [
    {"name": "liquidity", "outputs": [{"type": "uint128", "name": ""}],
     "inputs": [], "stateMutability": "view", "type": "function"},
    {"name": "slot0", "outputs": [
        {"type": "uint160", "name": "sqrtPriceX96"},
        {"type": "int24", "name": "tick"},
        {"type": "uint16", "name": "observationIndex"},
        {"type": "uint16", "name": "observationCardinality"},
        {"type": "uint16", "name": "observationCardinalityNext"},
        {"type": "uint8", "name": "feeProtocol"},
        {"type": "bool", "name": "unlocked"}
    ], "inputs": [], "stateMutability": "view", "type": "function"}
]

class DexClient:
    def __init__(self, web3: Web3, router_address: str):
        self.web3 = web3
        self.router = web3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=ROUTER_ABI
        )

    def detect_version(self, pair_address: str) -> str:
        try:
            self.web3.eth.contract(
                address=Web3.to_checksum_address(pair_address),
                abi=V2_PAIR_ABI
            ).functions.getReserves().call()
            return "v2"
        except Exception:
            pass
        try:
            self.web3.eth.contract(
                address=Web3.to_checksum_address(pair_address),
                abi=V3_POOL_ABI
            ).functions.liquidity().call()
            return "v3"
        except Exception:
            pass
        return "unknown"

    def has_min_liquidity(self, pair_address: str, weth_address: str, min_liq_weth: float = 0.5) -> bool:
        version = self.detect_version(pair_address)
        try:
            if version == "v2":
                reserves = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V2_PAIR_ABI
                ).functions.getReserves().call()
                reserve_weth = max(reserves[0], reserves[1]) / 1e18
                logger.info(f"[{pair_address}] Pool V2 - Liquidez: {reserve_weth:.4f} WETH")
                return reserve_weth >= min_liq_weth

            elif version == "v3":
                liq = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V3_POOL_ABI
                ).functions.liquidity().call()
                reserve_weth_equiv = liq / 1e18
                logger.info(f"[{pair_address}] Pool V3 - Liquidez equivalente: {reserve_weth_equiv:.4f} WETH")
                return reserve_weth_equiv >= min_liq_weth

            logger.warning(f"[{pair_address}] Tipo de pool desconhecido")
            return False
        except Exception as e:
            logger.error(f"Erro ao verificar liquidez ({version}): {e}", exc_info=True)
            return False

    def calc_dynamic_slippage(self, pair_address: str, weth_address: str, amount_in_eth: float) -> float:
        version = self.detect_version(pair_address)
        try:
            if version == "v2":
                reserves = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V2_PAIR_ABI
                ).functions.getReserves().call()
                reserve_weth = max(reserves[0], reserves[1]) / 1e18
                price_impact = amount_in_eth / reserve_weth
                slippage = min(max(price_impact * 1.5, 0.002), 0.02)

            elif version == "v3":
                liq = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V3_POOL_ABI
                ).functions.liquidity().call()
                reserve_weth_equiv = liq / 1e18
                price_impact = amount_in_eth / reserve_weth_equiv
                slippage = min(max(price_impact * 2, 0.0025), 0.025)

            else:
                slippage = 0.005

            logger.info(f"[{pair_address}] Slippage calculada: {slippage*100:.2f}%")
            return slippage
        except Exception as e:
            logger.error(f"Erro ao calcular slippage ({version}): {e}", exc_info=True)
            return 0.005

    def get_token_price(self, token_address: str, weth_address: str, amount_tokens: int = 10**18) -> float:
        """
        Retorna o preço de `amount_tokens` unidades do token em WETH.
        Usa getAmountsOut no router para converter token → WETH.
        """
        path = [
            Web3.to_checksum_address(token_address),
            Web3.to_checksum_address(weth_address)
        ]
        try:
            amounts = self.router.functions.getAmountsOut(amount_tokens, path).call()
            price = Decimal(amounts[-1]) / Decimal(10**18)
            logger.info(f"[Price] {amount_tokens / 10**18:.4f} token(s) ({token_address}) → {price:.6f} WETH")
            return float(price)
        except Exception as e:
            logger.error(f"Erro ao obter preço do token {token_address}: {e}", exc_info=True)
            return 0.0
