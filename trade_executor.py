import time
import logging
from threading import RLock
from typing import List, Optional
import datetime
from web3 import Web3

from exchange_client import ExchangeClient
from stratesniper import log_event, flush_report  # 🔹 importa do sniper

logger = logging.getLogger(__name__)

ERC20_DECIMALS_ABI = [
    {
        "type": "function",
        "name": "decimals",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    }
]

class TradeExecutor:
    """
    Executor básico de ordens de compra e venda com log consolidado.
    """

    def __init__(
        self,
        w3: Web3,
        wallet_address: str,
        trade_size_eth: float,
        slippage_bps: int,
        dry_run: bool = False,
        dedupe_ttl_sec: int = 5,
        alert=None   # 🔹 recebe para flush_report()
    ):
        self.w3 = w3
        self.wallet_address = wallet_address
        self.trade_size = trade_size_eth
        self.slippage_bps = slippage_bps
        self.dry_run = dry_run
        self.alert = alert

        self.simulated_pnl = 0.0
        self._lock = RLock()
        self._recent = {}       # {(side, token_in, token_out): timestamp}
        self._ttl = dedupe_ttl_sec

        self.client: Optional[ExchangeClient] = None
        self.open_positions_count = 0

    def set_exchange_client(self, client: ExchangeClient):
        self.client = client

    def _now(self) -> int:
        return int(time.time())

    def _key(self, side: str, token_in: str, token_out: str) -> tuple:
        return (
            side,
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
        )

    def _is_duplicate(self, side: str, token_in: str, token_out: str) -> bool:
        with self._lock:
            key = self._key(side, token_in, token_out)
            last_ts = self._recent.get(key, 0)
            if self._now() - last_ts < self._ttl:
                return True
            self._recent[key] = self._now()
            return False

    def _decimals(self, token_address: str) -> int:
        erc20 = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_DECIMALS_ABI
        )
        return int(erc20.functions.decimals().call())

    async def buy(self, path: List[str], amount_in_wei: int, amount_out_min: Optional[int] = None) -> Optional[str]:
        token_in, token_out = path[0], path[-1]
        log_event(f"[BUY] {token_in} → {token_out} | ETH={amount_in_wei} wei min_out={amount_out_min}")

        if self._is_duplicate("buy", token_in, token_out):
            log_event("[BUY] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            log_event(f"[DRY_RUN] Simulando compra: {token_in} → {token_out}")
            self.open_positions_count += 1
            return f"SIMULATED_BUY_{token_out}_{datetime.datetime.now().isoformat()}"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.buy_token(token_in, token_out, amount_in_wei, amount_out_min)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.open_positions_count += 1
            log_event(f"[BUY] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            log_event(f"[BUY] Falha ao executar compra: {e}")
            return None

    async def sell(self, path: List[str], amount_in_wei: int, min_out: Optional[int] = None) -> Optional[str]:
        token_in, token_out = path[0], path[-1]
        log_event(f"[SELL] {token_in} → {token_out} | amt={amount_in_wei} min_out={min_out}")

        if self._is_duplicate("sell", token_in, token_out):
            log_event("[SELL] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            log_event(f"[DRY_RUN] Simulando venda: {token_in} → {token_out}")
            self.open_positions_count = max(0, self.open_positions_count - 1)
            return f"SIMULATED_SELL_{token_in}_{datetime.datetime.now().isoformat()}"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.sell_token(token_in, token_out, amount_in_wei, min_out)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.open_positions_count = max(0, self.open_positions_count - 1)
            log_event(f"[SELL] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            log_event(f"[SELL] Falha ao executar venda: {e}")
            return None

    def stop(self):
        log_event("Executor encerrando ciclo.")
        if self.alert:
            flush_report(self.alert)

RealTradeExecutor = TradeExecutor

class SafeTradeExecutor(TradeExecutor):
    pass
