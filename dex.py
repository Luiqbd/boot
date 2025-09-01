import logging
from enum import Enum
from functools import lru_cache
from decimal import Decimal
from typing import Dict, List, Tuple, Union

from web3 import Web3
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ABIFunctionNotFound

# Mantenha seus ABIs carregados aqui (substitua ... pelos seus conteúdos)
ROUTER_ABI   : List[dict] = [...]
V2_PAIR_ABI  : List[dict] = [...]
V3_POOL_ABI  : List[dict] = [...]

logger = logging.getLogger(__name__)


class DexVersion(str, Enum):
    V2      = "v2"
    V3      = "v3"
    UNKNOWN = "unknown"


class DexClient:
    """
    Cliente para leitura de dados on-chain em pares/pools
    e cálculo de liquidez e slippage dinâmicos.
    """

    def __init__(self, web3: Web3, router_address: str):
        self.web3      = web3
        self.router    = self._contract(router_address, ROUTER_ABI)
        # cache para instâncias de Contract por endereço + ABI
        self._contract_cache: Dict[Tuple[str, Tuple[int, ...]], Contract] = {}

    def _contract(self, address: str, abi: List[dict]) -> Contract:
        """
        Retorna um objeto Contract para o endereço+ABI, armazenando em cache
        para evitar recriações.
        """
        checksum = Web3.to_checksum_address(address)
        key = (checksum, tuple(sorted(item.get("name","") for item in abi)))
        if key not in self._contract_cache:
            self._contract_cache[key] = self.web3.eth.contract(address=checksum, abi=abi)
        return self._contract_cache[key]

    @lru_cache(maxsize=128)
    def detect_version(self, pair_address: str) -> DexVersion:
        """
        Detecta se um par é V2 (UniswapV2 style) ou V3 (concentrated liquidity).
        Retorna DexVersion.UNKNOWN se não for reconhecido.
        """
        addr = Web3.to_checksum_address(pair_address)
        # tenta V2
        try:
            contract = self._contract(addr, V2_PAIR_ABI)
            # getReserves existe apenas em V2
            contract.functions.getReserves().call()
            return DexVersion.V2
        except (BadFunctionCallOutput, ABIFunctionNotFound):
            pass
        except Exception as e:
            logger.debug(f"detect_version V2 check falhou: {e}")

        # tenta V3
        try:
            contract = self._contract(addr, V3_POOL_ABI)
            # liquidity existe em V3 pools
            contract.functions.liquidity().call()
            return DexVersion.V3
        except (BadFunctionCallOutput, ABIFunctionNotFound):
            pass
        except Exception as e:
            logger.debug(f"detect_version V3 check falhou: {e}")

        return DexVersion.UNKNOWN

    def _get_reserves(
        self,
        pair_address: str
    ) -> Tuple[Decimal, Decimal]:
        """
        Retorna as duas reservas do par V2, em unidades de WETH (divididas por 1e18).
        Lança se não for V2 ou em caso de falha.
        """
        contract = self._contract(pair_address, V2_PAIR_ABI)
        r0, r1, _ = contract.functions.getReserves().call()
        return (Decimal(r0) / Decimal(1e18), Decimal(r1) / Decimal(1e18))

    def _get_liquidity_v3(self, pool_address: str) -> Decimal:
        """
        Retorna o valor de 'liquidity' de um pool V3, convertido para WETH units (1e18).
        """
        contract = self._contract(pool_address, V3_POOL_ABI)
        liq = contract.functions.liquidity().call()
        return Decimal(liq) / Decimal(1e18)

    def has_min_liquidity(
        self,
        pair_address: str,
        min_liq_weth: Union[float, Decimal] = Decimal("0.5")
    ) -> bool:
        """
        Verifica se o par/pool tem liquidez mínima de WETH.
        Retorna False em versão desconhecida ou erro.
        """
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
        Calcula slippage dinâmica com base no impacto de swap:
          - V2: slippage = clamp(impact * 1.5, 0.2%, 2%)  
          - V3: slippage = clamp(impact * 2,   0.25%, 2.5%)  
        Se versão unknown ou erro, retorna 0.5% (0.005).
        """
        version = self.detect_version(pair_address)
        amt_in = Decimal(str(amount_in_eth))

        try:
            if version == DexVersion.V2:
                r0, r1 = self._get_reserves(pair_address)
                reserve = max(r0, r1)
                impact = amt_in / reserve
                sl = impact * Decimal("1.5")
                sl = sl.quantize(Decimal("0.00000001"))
                return min(max(sl, Decimal("0.002")), Decimal("0.02"))

            if version == DexVersion.V3:
                reserve = self._get_liquidity_v3(pair_address)
                impact = amt_in / reserve
                sl = impact * Decimal("2")
                sl = sl.quantize(Decimal("0.00000001"))
                return min(max(sl, Decimal("0.0025")), Decimal("0.025"))

        except Exception as e:
            logger.error(f"Erro ao calcular slippage ({version}): {e}", exc_info=True)

        # fallback
        return Decimal("0.005")

    def get_token_price(
        self,
        token_address: str,
        weth_address: str,
        amount_tokens: int = 10**18
    ) -> Decimal:
        """
        Retorna o preço de `amount_tokens` do token em WETH (ex.: para 1 token = 1e18 base units).
        Se falhar, retorna 0.
        """
        try:
            path = [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(weth_address)
            ]
            amounts = self.router.functions.getAmountsOut(amount_tokens, path).call()
            price_weth = Decimal(amounts[-1]) / Decimal(1e18)
            logger.info(
                f"[Price] {Decimal(amount_tokens) / Decimal(1e18):.4f} token = {price_weth:.6f} WETH"
            )
            return price_weth

        except Exception as e:
            logger.error(f"Erro ao obter preço do token {token_address}: {e}", exc_info=True)
            return Decimal(0)
