import time
import logging
from decimal import Decimal, InvalidOperation
from threading import RLock
from web3 import Web3

logger = logging.getLogger(__name__)

# ABI mínima para fallback de consulta de decimals
ERC20_DECIMALS_ABI = [{
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}],
}]


class TradeExecutor:
    def __init__(self, exchange_client, dry_run: bool = False, dedupe_ttl_sec: int = 5):
        """
        :param exchange_client: instância do ExchangeClient já configurada
        :param dry_run: se True, não envia transações on-chain
        :param dedupe_ttl_sec: tempo mínimo entre operações iguais (segundos)
        """
        self.client = exchange_client
        self.dry_run = dry_run
        self._lock = RLock()
        self._recent = {}  # {(side, token_in, token_out): last_ts}
        self._ttl = dedupe_ttl_sec

    def _now(self) -> int:
        return int(time.time())

    def _normalize_addr(self, addr: str) -> str:
        try:
            if isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42:
                return Web3.to_checksum_address(addr)
        except Exception:
            pass
        return addr

    def _key(self, side: str, token_in: str, token_out: str) -> tuple:
        return (
            side,
            self._normalize_addr(token_in),
            self._normalize_addr(token_out),
        )

    def _is_duplicate(self, side, token_in, token_out) -> bool:
        with self._lock:
            key = self._key(side, token_in, token_out)
            last = self._recent.get(key, 0)
            if self._now() - last < self._ttl:
                return True
            self._recent[key] = self._now()
            return False

    def _to_wei_eth(self, amount_eth) -> int:
        try:
            amt = Decimal(str(amount_eth))
            if amt <= 0:
                raise ValueError("amount_eth deve ser > 0")
            return self.client.web3.to_wei(amt, "ether")
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Quantidade ETH inválida: {amount_eth} ({e})")

    def _to_base_units(self, amount_tokens, decimals: int) -> int:
        try:
            amt = Decimal(str(amount_tokens))
            if amt <= 0:
                raise ValueError("amount_tokens deve ser > 0")
            scale = Decimal(10) ** int(decimals)
            return int(amt * scale)
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Quantidade de tokens inválida: {amount_tokens} ({e})")

    def _decimals(self, token_address: str) -> int:
        # Prefere método do ExchangeClient
        if hasattr(self.client, "get_token_decimals"):
            return int(self.client.get_token_decimals(token_address))
        # Fallback direto on-chain
        erc20 = self.client.web3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_DECIMALS_ABI
        )
        return int(erc20.functions.decimals().call())

    def buy(self, token_in: str, token_out: str, amount_eth, amount_out_min: int | None = None):
        """
        token_in: WETH/ETH, token_out: TOKEN
        amount_eth: valor em ETH (humano)
        """
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
            tx_hash = self.client.buy_token(
                token_in_weth=token_in,
                token_out=token_out,
                amount_in_wei=amount_wei,
                amount_out_min=amount_out_min
            )
            tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            logger.info(f"Compra executada — pair={token_in}->{token_out} eth={amount_eth} tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao executar compra: {e}", exc_info=True)
            return None

    def sell(self, token_in: str, token_out: str, amount_tokens, amount_out_min: int | None = None):
        """
        token_in: TOKEN, token_out: WETH/ETH
        amount_tokens: quantidade humana (ex.: 1.5 tokens)
        """
        if self._is_duplicate("sell", token_in, token_out):
            logger.warning("Ordem de venda duplicada recente — ignorando")
            return None

        try:
            decimals = self._decimals(token_in)
            amount_base = self._to_base_units(amount_tokens, decimals)
        except Exception as e:
            logger.error(f"Falha ao preparar venda: {e}")
            return None

        if self.dry_run:
            logger.info(f"[DRY_RUN] Venda simulada {token_in}->{token_out} tokens={amount_tokens}")
            return "0xDRYRUN"

        try:
            tx_hash = self.client.sell_token(
                token_in=token_in,
                token_out_weth=token_out,
                amount_in_base_units=amount_base,
                amount_out_min=amount_out_min
            )
            tx_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
            logger.info(f"Venda executada — pair={token_in}->{token_out} tokens={amount_tokens} tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"Erro ao executar venda: {e}", exc_info=True)
            return None
