import time
import logging
import datetime
import inspect
from threading import RLock
from typing import List, Optional
from web3 import Web3

from exchange_client import ExchangeClient
from risk_manager import RiskManager
from stratesniper import log_event, flush_report  # 🔹 funções do sniper

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
        alert=None
    ):
        self.w3 = w3
        self.wallet_address = wallet_address
        self.trade_size = trade_size_eth
        self.slippage_bps = slippage_bps
        self.dry_run = dry_run
        self.alert = alert

        self.simulated_pnl = 0.0
        self._lock = RLock()
        self._recent = {}
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
        log_event(f"[BUY] {token_in} → {token_out} | ETH={amount_in_wei} min_out={amount_out_min}")

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


class SafeTradeExecutor:
    """
    Envolve o TradeExecutor com checagens de risco e integra ao relatório consolidado.
    """

    def __init__(
        self,
        executor: TradeExecutor,
        max_trade_size_eth: float,
        slippage_bps: int,
        alert=None
    ):
        self.executor = executor
        self.risk = RiskManager(
            max_trade_size=max_trade_size_eth,
            slippage_bps=slippage_bps
        )
        self.alert = alert

    def _can_trade(
        self,
        current_price: float,
        last_trade_price: float,
        direction: str,
        amount_eth: float
    ) -> bool:
        try:
            sig = inspect.signature(self.risk.can_trade)
            params = sig.parameters
            kwargs = {}
            if "current_price" in params:
                kwargs["current_price"] = current_price
            if "last_trade_price" in params:
                kwargs["last_trade_price"] = last_trade_price
            if "direction" in params:
                kwargs["direction"] = direction
            if "trade_size_eth" in params:
                kwargs["trade_size_eth"] = amount_eth
            elif "amount_eth" in params:
                kwargs["amount_eth"] = amount_eth

            allowed = self.risk.can_trade(**kwargs)
            log_event(f"[RISK] can_trade {kwargs} -> {allowed}")
            return allowed
        except Exception as e:
            log_event(f"[RISK] Erro em can_trade: {e}")
            return False

    def _register(self, sucesso: bool):
        try:
            self.risk.register_trade(success=sucesso)
            log_event(f"[RISK] Registro de trade: sucesso={sucesso}")
        except Exception as e:
            log_event(f"[RISK] Erro ao registrar trade: {e}")

    async def buy(
        self,
        path: list,
        amount_in_wei: int,
        amount_out_min: Optional[int],
        current_price: float,
        last_trade_price: float
    ) -> Optional[str]:
        if not self._can_trade(current_price, last_trade_price, "buy", self.executor.trade_size):
            log_event("[SAFE BUY] Bloqueada pelo RiskManager")
            return None

        tx = await self.executor.buy(path, amount_in_wei, amount_out_min)
        self._register(sucesso=(tx is not None))
        return tx

    async def sell(
        self,
        path: list,
        amount_in_wei: int,
        min_out: Optional[int],
        current_price: float,
        last_trade_price: float
    ) -> Optional[str]:
        if not self._can_trade(current_price, last_trade_price, "sell", self.executor.trade_size):
            log_event("[SAFE SELL] Bloqueada pelo RiskManager")
            return None

        tx = await self.executor.sell(path, amount_in_wei, min_out)
        self._register(sucesso=(tx is not None))
        return tx

    def record_outcome(self, loss_eth: float = 0.0):
        if loss_eth <= 0:
            return
        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.register_loss(loss_eth)
                log_event(f"[RISK] Registrado prejuízo de {loss_eth} ETH")
        except Exception as e:
            log_event(f"[RISK] Erro ao registrar perda: {e}")

    def stop(self):
        log_event("[SAFE EXECUTOR] Encerrando ciclo.")
        if self.alert:
            flush_report(self.alert)
