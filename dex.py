import os
import json
import logging
from web3 import Web3
from decimal import Decimal
from config import config  # gas, deadline e slippage do ambiente

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- ABI mínimas ---
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
    def __init__(self, web3, router_address):
        self.web3 = web3
        abi_path = os.path.join(os.path.dirname(__file__), "abis", "uniswap_router.json")
        with open(abi_path) as f:
            router_abi = json.load(f)
        self.router = web3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=router_abi
        )

    def detect_version(self, pair_address):
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

    def has_min_liquidity(self, pair_address, weth_address, min_liq_weth=0.5):
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

    def calc_dynamic_slippage(self, pair_address, weth_address, amount_in_eth):
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

    def get_token_price(self, token_address, weth_address, amount_tokens=10**18):
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
            logger.info(f"[Price] 1 token ({token_address}) → {price:.6f} WETH")
            return float(price)
        except Exception as e:
            logger.error(f"Erro ao obter preço do token {token_address}: {e}", exc_info=True)
            return 0.0

# --- Função principal de entrada ---
def on_new_pair(pair_addr, target_token, dex_info, weth, min_liq_eth):
    if not rate_limiter.allow():
        return
    try:
        dex_client = DexClient(web3, dex_info["router"])

        if not dex_client.has_min_liquidity(pair_addr, weth, min_liq_eth):
            logger.info(f"[{pair_addr}] Reprovado no filtro de liquidez")
            return

        if is_honeypot(target_token):
            logger.warning(f"[{pair_addr}] Token é honeypot, descartando")
            return

        tax = get_token_tax(target_token)
        if tax > MAX_TAX_ALLOWED:
            logger.warning(f"[{pair_addr}] Taxa {tax*100:.2f}% acima do limite")
            return

        price = dex_client.get_token_price(target_token, weth)
        logger.info(f"[{pair_addr}] Preço inicial do token: {price:.6f} WETH")

        slip_limit = dex_client.calc_dynamic_slippage(pair_addr, weth, ENTRY_SIZE_ETH)
        logger.info(f"[{pair_addr}] Executando compra com slippage {slip_limit*100:.2f}%")

        buy_token(target_token, amount_in_eth=ENTRY_SIZE_ETH, slippage=slip_limit)

    except Exception as e:
        logger.error(f"Erro no processamento do par {pair_addr}: {e}", exc_info=True)
