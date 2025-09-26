# dex_client.py

import json
import logging
from enum import Enum
from functools import lru_cache
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple, Union

try:
    from web3 import Web3
    from web3.contract import Contract
    from web3.exceptions import BadFunctionCallOutput, ABIFunctionNotFound, ContractLogicError
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
    Contract = None
    BadFunctionCallOutput = Exception
    ABIFunctionNotFound = Exception
    ContractLogicError = Exception

from config import config


# CONFIGURAÇÃO DE PATHS PARA OS ABIS
BASE_DIR = Path(__file__).parent
ABIS_DIR = BASE_DIR / "abis"

with open(ABIS_DIR / "uniswap_router.json", encoding="utf-8") as f:
    ROUTER_ABI = json.load(f)
with open(ABIS_DIR / "uniswap_v2_pair.json", encoding="utf-8") as f:
    V2_PAIR_ABI = json.load(f)
with open(ABIS_DIR / "uniswap_v3_pool.json", encoding="utf-8") as f:
    V3_POOL_ABI = json.load(f)

logger = logging.getLogger(__name__)


class DexVersion(str, Enum):
    V2 = "v2"
    V3 = "v3"
    UNKNOWN = "unknown"


class DexClient:
    """
    Cliente on-chain para pares/pools:
      - detecta versão (V2 ou V3) via ABI;
      - lê reservas ou liquidez;
      - checa condição mínima (usando config['MIN_LIQ_WETH']);
      - calcula slippage dinâmica.
    """
    def __init__(self, web3: Web3, router_address: str):
        self.web3 = web3
        self._contract_cache: Dict[Tuple[str, Tuple[str, ...]], Contract] = {}
        self.router = self._contract(router_address, ROUTER_ABI)

    def _contract(self, address: str, abi: List[dict]) -> Contract:
        checksum = Web3.to_checksum_address(address)
        key = (checksum, tuple(sorted(item.get("name", "") for item in abi)))
        if key not in self._contract_cache:
            logger.debug(f"Caching new contract {checksum}")
            self._contract_cache[key] = self.web3.eth.contract(
                address=checksum,
                abi=abi
            )
        return self._contract_cache[key]

    @lru_cache(maxsize=128)
    def detect_version(self, pair_address: str) -> DexVersion:
        addr = Web3.to_checksum_address(pair_address)
        # V2?
        try:
            c = self._contract(addr, V2_PAIR_ABI)
            c.functions.getReserves().call()
            return DexVersion.V2
        except (BadFunctionCallOutput, ABIFunctionNotFound):
            pass
        except Exception as e:
            logger.debug(f"detect_version V2 check falhou: {e}")
        # V3?
        try:
            c = self._contract(addr, V3_POOL_ABI)
            c.functions.liquidity().call()
            return DexVersion.V3
        except (BadFunctionCallOutput, ABIFunctionNotFound):
            pass
        except Exception as e:
            logger.debug(f"detect_version V3 check falhou: {e}")
        return DexVersion.UNKNOWN

    def _get_reserves(self, pair_address: str) -> Tuple[Decimal, Decimal]:
        contract = self._contract(pair_address, V2_PAIR_ABI)
        r0, r1, _ = contract.functions.getReserves().call()
        return Decimal(r0) / Decimal(1e18), Decimal(r1) / Decimal(1e18)

    def _get_liquidity_v3(self, pool_address: str) -> Decimal:
        contract = self._contract(pool_address, V3_POOL_ABI)
        liq = contract.functions.liquidity().call()
        return Decimal(liq) / Decimal(1e18)

    def has_min_liquidity(
        self,
        pair_address: str,
        min_liq_weth: Union[float, Decimal] = None
    ) -> bool:
        """
        Verifica se o par/pool tem liquidez mínima em WETH.
        Se min_liq_weth não for passado, usa config["MIN_LIQ_WETH"].
        """
        if min_liq_weth is None:
            min_liq_weth = config["MIN_LIQ_WETH"]

        version = self.detect_version(pair_address)
        try:
            if version == DexVersion.V2:
                r0, r1 = self._get_reserves(pair_address)
                reserve_weth = max(r0, r1)
                logger.info(f"[{pair_address}] V2 liquidez = {reserve_weth:.4f} WETH")
                return reserve_weth >= Decimal(min_liq_weth)
            if version == DexVersion.V3:
                reserve_eq = self._get_liquidity_v3(pair_address)
                logger.info(f"[{pair_address}] V3 liquidez eq = {reserve_eq:.4f} WETH")
                return reserve_eq >= Decimal(min_liq_weth)
            logger.warning(f"[{pair_address}] Tipo de pool desconhecido: {version}")
            return False
        except Exception as e:
            logger.error(f"Erro ao verificar liquidez ({version}): {e}", exc_info=True)
            return False

    def calc_dynamic_slippage(
        self,
        pair_address: str,
        amount_in_eth: Union[float, Decimal]
    ) -> Decimal:
        """
        Retorna slippage dinâmica (fracional) com base na liquidez.
        """
        version = self.detect_version(pair_address)
        amt_in = Decimal(str(amount_in_eth))
        try:
            if version == DexVersion.V2:
                r0, r1 = self._get_reserves(pair_address)
                reserve = max(r0, r1)
                impact = amt_in / reserve
                sl = (impact * Decimal("1.5")).quantize(Decimal("0.00000001"))
                return min(max(sl, Decimal("0.002")), Decimal("0.02"))
            if version == DexVersion.V3:
                reserve = self._get_liquidity_v3(pair_address)
                impact = amt_in / reserve
                sl = (impact * Decimal("2")).quantize(Decimal("0.00000001"))
                return min(max(sl, Decimal("0.0025")), Decimal("0.025"))
        except Exception as e:
            logger.error(f"Erro ao calcular slippage ({version}): {e}", exc_info=True)
        # fallback genérico
        return Decimal("0.005")

    def get_token_price(
        self,
        token_address: str,
        weth_address: str,
        amount_tokens: int = 10**18
    ) -> Union[Decimal, None]:
        """
        Retorna o preço de `amount_tokens` do token em WETH.
        Se falhar, retorna None.
        """
        try:
            path = [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(weth_address)
            ]
            amounts = self.router.functions.getAmountsOut(amount_tokens, path).call()
            price_weth = Decimal(amounts[-1]) / Decimal(1e18)
            logger.info(
                f"[Price] {Decimal(amount_tokens) / Decimal(1e18):.4f} token = "
                f"{price_weth:.6f} WETH"
            )
            return price_weth
        except ContractLogicError as e:
            logger.warning(f"Revert ao obter preço do token {token_address}: {e}")
            return None
        except Exception as e:
            logger.error(
                f"Erro inesperado ao obter preço do token {token_address}: {e}",
                exc_info=True
            )
            return None
