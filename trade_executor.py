import time
import logging
from decimal import Decimal, InvalidOperation
from threading import RLock

logger = logging.getLogger(__name__)

class TradeExecutor:
    def __init__(self, exchange_client, dry_run: bool = False, dedupe_ttl_sec: int = 5):
        self.client = exchange_client
        self.dry_run = dry_run
        self._lock = RLock()
        self._recent = {}  # {(side, token_in, token_out): last_ts}
        self._ttl = dedupe_ttl_sec

    def _now(self):
        return int(time.time())

    def _is_duplicate(self, side, token_in, token_out):
        with self._lock:
            key = (side, token_in, token_out)
            last = self._recent.get(key, 0)
            if self._now() - last < self._ttl:
                return True
            self._recent[key] = self._now()
            return False

    def _to_wei_eth(self, amount_eth):
        try:
            amt = Decimal(str(amount_eth))
            if amt <= 0:
                raise ValueError("amount_eth deve ser > 0")
            return self.client.web3.to_wei(amt, "ether")
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Quantidade ETH inválida: {amount_eth} ({e})")

    def _to_base_units(self, amount_tokens, decimals: int):
        try:
            amt = Decimal(str(amount_tokens))
            if amt <= 0:
                raise ValueError("amount_tokens deve ser > 0")
            scale = Decimal(10) ** decimals
            return int(amt * scale)
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Quantidade de tokens inválida: {amount_tokens} ({e})")

    def buy(self, token_in, token_out, amount_eth):
        # token_in: WETH/ETH, token_out: TOKEN
        if self._is_duplicate("buy", token_in, token_out):
            logger.warning("Ordem de compra duplicada recente — ignorando")
            return None

        try:
            amount_wei = self._to_wei_eth(amount_eth)
        except Exception as e:
            logger.error(f"Falha na validação de amount_eth: {e}")
            return None

        if self.dry_run:
            logger.info(f"[DRY_RUN] Compra simulada {token_in}->{token_out} amount_eth={amount_eth}")
            return "0xDRYRUN"

        try:
            tx_hash = self.client.buy_token(token_in, token_out, amount_wei)
            tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            logger.info(f"Compra executada — pair={token_in}->{token_out} eth={amount_eth} tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao executar compra: {e}")
            return None

    def sell(self, token_in, token_out, amount_tokens):
        # token_in: TOKEN, token_out: WETH/ETH — amount_tokens é na unidade humana
        if self._is_duplicate("sell", token_in, token_out):
            logger.warning("Ordem de venda duplicada recente — ignorando")
            return None

        try:
            decimals = self.client.get_token_decimals(token_in)  # requer implementação no exchange_client
            amount_base = self._to_base_units(amount_tokens, decimals)
        except Exception as e:
            logger.error(f"Falha ao preparar venda: {e}")
            return None

        if self.dry_run:
            logger.info(f"[DRY_RUN] Venda simulada {token_in}->{token_out} tokens={amount_tokens}")
            return "0xDRYRUN"

        try:
            tx_hash = self.client.sell_token(token_in, token_out, amount_base)
            tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            logger.info(f"Venda executada — pair={token_in}->{token_out} tokens={amount_tokens} tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao executar venda: {e}")
            return None
