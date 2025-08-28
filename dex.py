# dex.py
import os
import logging
from decimal import Decimal
from web3 import Web3
from utils import to_float
from config import config

logger = logging.getLogger(__name__)

# ABIs mínimas
ROUTER_ABI = [ ... ]    # permaneçam como estavam no seu projeto
V2_PAIR_ABI = [ ... ]
V3_POOL_ABI = [ ... ]

class DexClient:
    def __init__(self, web3: Web3, router_address: str):
        self.web3   = web3
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
                logger.info(f"[{pair_address}] V2 Liquidez: {reserve_weth:.4f} WETH")
                return reserve_weth >= min_liq_weth

            if version == "v3":
                liq = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V3_POOL_ABI
                ).functions.liquidity().call()
                reserve_eq = liq / 1e18
                logger.info(f"[{pair_address}] V3 Liquidez equiv: {reserve_eq:.4f} WETH")
                return reserve_eq >= min_liq_weth

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
                impact = amount_in_eth / reserve_weth
                sl = min(max(impact * 1.5, 0.002), 0.02)

            elif version == "v3":
                liq = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V3_POOL_ABI
                ).functions.liquidity().call()
                reserve_eq = liq / 1e18
                impact = amount_in_eth / reserve_eq
                sl = min(max(impact * 2, 0.0025), 0.025)

            else:
                sl = 0.005

            logger.info(f"[{pair_address}] Slippage calculada: {sl*100:.2f}%")
            return sl

        except Exception as e:
            logger.error(f"Erro ao calcular slippage ({version}): {e}", exc_info=True)
            return 0.005

    def get_token_price(self, token_address: str, weth_address: str, amount_tokens: int = 10**18) -> float:
        try:
            path = [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(weth_address)
            ]
            amounts = self.router.functions.getAmountsOut(amount_tokens, path).call()
            price = Decimal(amounts[-1]) / Decimal(10**18)
            logger.info(f"[Price] {amount_tokens/1e18:.4f} token → {price:.6f} WETH")
            return float(price)
        except Exception as e:
            logger.error(f"Erro ao obter preço do token {token_address}: {e}", exc_info=True)
            return 0.0
