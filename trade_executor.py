# trade_executor.py

import time
import uuid
import logging
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any, Optional, Tuple, Union, Dict

try:
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
    BadFunctionCallOutput = Exception

logger = logging.getLogger(__name__)

# ABI mínimo para ler decimals de tokens ERC20
ERC20_DECIMALS_ABI = [{
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}],
}]


class TradeExecutor:
    """
    Gerencia ordens de compra e venda:
      - valida endereços e valores;
      - previne duplicatas no curto prazo;
      - cache de decimais de tokens;
      - suporte a dry-run (TX fake).
    """

    def __init__(
        self,
        exchange_client: Any,
        dry_run: bool = False,
        dedupe_ttl_sec: int = 5,
        decimals_ttl_sec: int = 300
    ):
        self.client = exchange_client
        self.dry_run = dry_run
        self._lock = RLock()
        self._recent: Dict[Tuple[str, str, str], int] = {}
        self._dedupe_ttl = dedupe_ttl_sec
        self._decimals_ttl = decimals_ttl_sec
        self._decimals_cache: Dict[str, Tuple[int, int]] = {}

    def _agora(self) -> int:
        return int(time.time())

    def _limpar_duplicatas(self) -> None:
        limite = self._agora() - self._dedupe_ttl
        with self._lock:
            chaves = [k for k, ts in self._recent.items() if ts < limite]
            for k in chaves:
                self._recent.pop(k, None)

    def _normalizar_endereco(self, addr: str) -> str:
        if not isinstance(addr, str):
            raise ValueError(f"Endereço deve ser string, não {type(addr)}")
        if not Web3.is_address(addr):
            raise ValueError(f"Endereço inválido: {addr}")
        return Web3.to_checksum_address(addr)

    def _chave_duplicata(self, lado: str, token_in: str, token_out: str) -> Tuple[str, str, str]:
        return (
            lado,
            self._normalizar_endereco(token_in),
            self._normalizar_endereco(token_out)
        )

    def _eh_duplicata(self, lado: str, token_in: str, token_out: str) -> bool:
        self._limpar_duplicatas()
        chave = self._chave_duplicata(lado, token_in, token_out)
        agora = self._agora()
        with self._lock:
            ts = self._recent.get(chave)
            if ts and (agora - ts) < self._dedupe_ttl:
                return True
            self._recent[chave] = agora
            return False

    def _para_wei_eth(self, valor_eth: Union[str, float, Decimal]) -> int:
        try:
            amt = Decimal(str(valor_eth))
            if amt <= 0:
                raise ValueError("Valor deve ser > 0")
            return self.client.web3.to_wei(amt, "ether")
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"ETH inválido ({valor_eth}): {e}")

    def _buscar_decimais(self, token_addr: str) -> int:
        agora = self._agora()
        addr_norm = self._normalizar_endereco(token_addr)

        # retorna cache se válido
        if addr_norm in self._decimals_cache:
            dec, ts = self._decimals_cache[addr_norm]
            if agora - ts < self._decimals_ttl:
                return dec

        # busca via client ou ABI fallback
        if hasattr(self.client, "get_token_decimals"):
            dec = int(self.client.get_token_decimals(addr_norm))
        else:
            contrato = self.client.web3.eth.contract(address=addr_norm, abi=ERC20_DECIMALS_ABI)
            try:
                dec = int(contrato.functions.decimals().call())
            except BadFunctionCallOutput as e:
                raise ValueError(f"Falha ao ler decimals de {addr_norm}: {e}")

        self._decimals_cache[addr_norm] = (dec, agora)
        return dec

    def _para_unidade_base(self, tokens: Union[str, float, Decimal], decimais: int) -> int:
        try:
            amt = Decimal(str(tokens))
            if amt <= 0:
                raise ValueError("Tokens deve ser > 0")
            escala = Decimal(10) ** decimais
            return int(amt * escala)
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Tokens inválido ({tokens}): {e}")

    def comprar(
        self,
        token_in: str,
        token_out: str,
        amount_eth: Union[str, float, Decimal],
        amount_out_min: Optional[int] = None
    ) -> Optional[str]:
        """
        Compra token_out usando WETH (token_in).
        Retorna tx_hash real ou fake (dry_run) ou None em caso de duplicata/erro.
        """
        if self._eh_duplicata("buy", token_in, token_out):
            logger.warning(f"Compra duplicada ignorada: {token_in}->{token_out}")
            return None

        try:
            wei = self._para_wei_eth(amount_eth)
        except ValueError as e:
            logger.error(f"Falha validação ETH: {e}", exc_info=True)
            return None

        if self.dry_run:
            fake = f"0xDRY{uuid.uuid4().hex[:12]}"
            logger.info(f"[DRY_RUN] BUY {token_in}->{token_out}, ETH={amount_eth} → TX {fake}")
            return fake

        try:
            txh = self.client.buy_token(
                token_in_weth=token_in,
                token_out=token_out,
                amount_in_wei=wei,
                amount_out_min=amount_out_min
            )
            tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
            logger.info(f"Compra enviada {token_in}->{token_out}, ETH={amount_eth} → TX {tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao enviar compra {token_in}->{token_out}", exc_info=True)
            return None

    def vender(
        self,
        token_in: str,
        token_out: str,
        amount_tokens: Union[str, float, Decimal],
        amount_out_min: Optional[int] = None
    ) -> Optional[str]:
        """
        Vende token_in para WETH (token_out).
        Retorna tx_hash real ou fake (dry_run) ou None em caso de duplicata/erro.
        """
        if self._eh_duplicata("sell", token_in, token_out):
            logger.warning(f"Venda duplicada ignorada: {token_in}->{token_out}")
            return None

        try:
            dec = self._buscar_decimais(token_in)
            base_units = self._para_unidade_base(amount_tokens, dec)
        except ValueError as e:
            logger.error(f"Falha preparação venda: {e}", exc_info=True)
            return None

        if self.dry_run:
            fake = f"0xDRY{uuid.uuid4().hex[:12]}"
            logger.info(f"[DRY_RUN] SELL {token_in}->{token_out}, tokens={amount_tokens} → TX {fake}")
            return fake

        try:
            txh = self.client.sell_token(
                token_in=token_in,
                token_out_weth=token_out,
                amount_in_base_units=base_units,
                amount_out_min=amount_out_min
            )
            tx_hex = txh.hex() if hasattr(txh, "hex") else str(txh)
            logger.info(f"Venda enviada {token_in}->{token_out}, tokens={amount_tokens} → TX {tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao enviar venda {token_in}->{token_out}", exc_info=True)
            return None
