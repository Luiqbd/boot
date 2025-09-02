import time
import logging
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Optional, Tuple, Union, Dict

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput

logger = logging.getLogger(__name__)

ERC20_DECIMALS_ABI = [{
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}],
}]


class TradeExecutor:
    """
    Orquestra ordens de compra e venda com:
    - deduplicação de chamadas
    - validação de parâmetros (endereços, valores ETH/tokens)
    - suporte a dry-run para testes
    """

    def __init__(
        self,
        exchange_client,
        dry_run: bool = False,
        dedupe_ttl_sec: int = 5,
        decimals_ttl_sec: int = 300
    ) -> None:
        self.client = exchange_client
        self.dry_run = dry_run

        # Controle de duplicatas (side, token_in, token_out) -> timestamp
        self._lock = RLock()
        self._recent: Dict[Tuple[str, str, str], int] = {}
        self._dedupe_ttl = dedupe_ttl_sec

        # Cache de decimais: token_address -> (decimals, last_fetched_ts)
        self._decimals_cache: Dict[str, Tuple[int, int]] = {}
        self._decimals_ttl = decimals_ttl_sec

    def _now(self) -> int:
        return int(time.time())

    def _cleanup_recent(self) -> None:
        """Descarta chaves antigas para manter o cache de duplicatas enxuto."""
        cutoff = self._now() - self._dedupe_ttl
        with self._lock:
            stale = [k for k, t in self._recent.items() if t < cutoff]
            for k in stale:
                self._recent.pop(k, None)

    def _normalize_addr(self, addr: str) -> str:
        """
        Valida e retorna o endereço em checksum.
        Levanta ValueError se o formato for inválido.
        """
        if not isinstance(addr, str):
            raise ValueError(f"Endereço deve ser string, recebeu {type(addr)}")
        if not Web3.is_address(addr):
            raise ValueError(f"Endereço inválido: {addr}")
        return Web3.to_checksum_address(addr)

    def _make_key(self, side: str, token_in: str, token_out: str) -> Tuple[str, str, str]:
        return (
            side,
            self._normalize_addr(token_in),
            self._normalize_addr(token_out)
        )

    def _is_duplicate(self, side: str, token_in: str, token_out: str) -> bool:
        """
        Retorna True se uma ordem idêntica foi enviada no intervalo de TTL.
        """
        self._cleanup_recent()
        key = self._make_key(side, token_in, token_out)
        now = self._now()
        with self._lock:
            last = self._recent.get(key)
            if last and (now - last) < self._dedupe_ttl:
                return True
            self._recent[key] = now
            return False

    def _to_wei_eth(self, amount_eth: Union[str, float, Decimal]) -> int:
        """
        Converte um valor ETH para wei, validando > 0.
        Levanta ValueError em caso de formato incorreto.
        """
        try:
            amt = Decimal(str(amount_eth))
            if amt <= 0:
                raise ValueError("deve ser > 0")
            return self.client.web3.to_wei(amt, "ether")
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"ETH inválido ({amount_eth}): {e}")

    def _fetch_decimals(self, token_address: str) -> int:
        """
        Lê ou reutiliza o número de decimais (ERC20).
        Usa cache por _decimals_ttl segundos.
        """
        now = self._now()
        addr = self._normalize_addr(token_address)

        cached = self._decimals_cache.get(addr)
        if cached:
            dec, ts = cached
            if now - ts < self._decimals_ttl:
                return dec

        if hasattr(self.client, "get_token_decimals"):
            dec = int(self.client.get_token_decimals(addr))
        else:
            contract = self.client.web3.eth.contract(address=addr, abi=ERC20_DECIMALS_ABI)
            try:
                dec = int(contract.functions.decimals().call())
            except BadFunctionCallOutput as e:
                raise ValueError(f"Falha ao ler decimals em {addr}: {e}")

        self._decimals_cache[addr] = (dec, now)
        return dec

    def _to_base_units(self, amount_tokens: Union[str, float, Decimal], decimals: int) -> int:
        """
        Converte quantidade de tokens para base units (inteiro),
        validando > 0.
        """
        try:
            amt = Decimal(str(amount_tokens))
            if amt <= 0:
                raise ValueError("deve ser > 0")
            scale = Decimal(10) ** decimals
            return int(amt * scale)
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Tokens inválido ({amount_tokens}): {e}")

    def buy(
        self,
        token_in: str,
        token_out: str,
        amount_eth: Union[str, float, Decimal],
        amount_out_min: Optional[int] = None
    ) -> Optional[str]:
        """
        Executa ordem de compra de token_out usando WETH (token_in).
        Retorna hash da tx ou None em caso de fail/duplicata.
        """
        if self._is_duplicate("buy", token_in, token_out):
            logger.warning(
                "Compra duplicada — ignorada",
                extra={"side": "buy", "in": token_in, "out": token_out}
            )
            return None

        try:
            amt_wei = self._to_wei_eth(amount_eth)
        except ValueError as e:
            logger.error(
                f"Falha validação ETH: {e}",
                exc_info=True,
                extra={"amount_eth": amount_eth}
            )
            return None

        if self.dry_run:
            logger.info(
                "DRY_RUN buy",
                extra={"pair": f"{token_in}->{token_out}", "eth": amount_eth}
            )
            return "0xDRYRUN"

        try:
            txh = self.client.buy_token(
                token_in_weth=token_in,
                token_out=token_out,
                amount_in_wei=amt_wei,
                amount_out_min=amount_out_min
            )
            tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
            logger.info(
                "Compra enviada",
                extra={"pair": f"{token_in}->{token_out}", "eth": amount_eth, "tx": tx_hex}
            )
            return tx_hex
        except Exception as e:
            logger.error(
                "Erro ao comprar token",
                exc_info=True,
                extra={"pair": f"{token_in}->{token_out}"}
            )
            return None

    def sell(
        self,
        token_in: str,
        token_out: str,
        amount_tokens: Union[str, float, Decimal],
        amount_out_min: Optional[int] = None
    ) -> Optional[str]:
        """
        Executa ordem de venda de token_in para WETH (token_out).
        Retorna hash da tx ou None em caso de fail/duplicata.
        """
        if self._is_duplicate("sell", token_in, token_out):
            logger.warning(
                "Venda duplicada — ignorada",
                extra={"side": "sell", "in": token_in, "out": token_out}
            )
            return None

        try:
            decimals = self._fetch_decimals(token_in)
            amt_base = self._to_base_units(amount_tokens, decimals)
        except ValueError as e:
            logger.error(
                f"Falha preparação de venda: {e}",
                exc_info=True,
                extra={"amount_tokens": amount_tokens}
            )
            return None

        if self.dry_run:
            logger.info(
                "DRY_RUN sell",
                extra={"pair": f"{token_in}->{token_out}", "tokens": amount_tokens}
            )
            return "0xDRYRUN"

        try:
            txh = self.client.sell_token(
                token_in=token_in,
                token_out_weth=token_out,
                amount_in_base_units=amt_base,
                amount_out_min=amount_out_min
            )
            tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
            logger.info(
                "Venda enviada",
                extra={"pair": f"{token_in}->{token_out}", "tokens": amount_tokens, "tx": tx_hex}
            )
            return tx_hex
        except Exception as e:
            logger.error(
                "Erro ao vender token",
                exc_info=True,
                extra={"pair": f"{token_in}->{token_out}"}
            )
            return None
