import time
import logging
import datetime
import inspect
from threading import RLock
from typing import List, Optional, Tuple
from web3 import Web3

from exchange_client import ExchangeClient
from risk_manager import RiskManager

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
    Executor básico de ordens de compra e venda com log consolidado no Telegram.
    Aceita tanto amount_in_wei/amountoutmin quanto amountinwei/amount_out_min (compat).
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
        # Compat: disponibiliza ambos os nomes
        self.trade_size = trade_size_eth
        self.tradesize = trade_size_eth
        self.slippage_bps = slippage_bps
        self.dry_run = dry_run
        self.alert = alert

        self.simulated_pnl = 0.0
        self._lock = RLock()
        self._recent: dict[Tuple[str, str, str], int] = {}
        self._ttl = dedupe_ttl_sec

        self.client: Optional[ExchangeClient] = None
        self.open_positions_count = 0

    # Compat: ambos métodos para configurar o cliente
    def set_exchange_client(self, client: ExchangeClient):
        self.client = client

    def setexchangeclient(self, client: ExchangeClient):
        self.set_exchange_client(client)

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

    def _log(self, msg: str):
        if self.alert:
            try:
                self.alert.log_event(msg)
            except Exception as e:
                logger.warning(f"Falha ao enviar para alert: {e}")
        logger.info(msg)

    async def buy(
        self,
        path: List[str],
        amount_in_wei: Optional[int] = None,
        amountinwei: Optional[int] = None,
        amount_out_min: Optional[int] = None,
        amountoutmin: Optional[int] = None
    ) -> Optional[str]:
        token_in, token_out = path[0], path[-1]
        amt_in = amount_in_wei if amount_in_wei is not None else amountinwei
        min_out = amount_out_min if amount_out_min is not None else amountoutmin

        self._log(f"[BUY] {token_in} → {token_out} | inWei={amt_in} minOut={min_out}")

        if self._is_duplicate("buy", token_in, token_out):
            self._log("[BUY] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            tx_hash = f"SIMULATED_BUY_{token_out}_{datetime.datetime.now().isoformat()}"
            self.open_positions_count += 1
            self._log(f"[DRY_RUN] Compra simulada: {tx_hash}")
            return tx_hash

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.buy_token(token_in, token_out, amt_in, min_out)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.open_positions_count += 1
            self._log(f"[BUY] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            self._log(f"[BUY] Falha ao executar compra: {e}")
            return None

    async def sell(
        self,
        path: List[str],
        amount_in_wei: Optional[int] = None,
        amountinwei: Optional[int] = None,
        min_out: Optional[int] = None,
        minout: Optional[int] = None
    ) -> Optional[str]:
        token_in, token_out = path[0], path[-1]
        amt_in = amount_in_wei if amount_in_wei is not None else amountinwei
        min_out_val = min_out if min_out is not None else minout

        self._log(f"[SELL] {token_in} → {token_out} | inWei={amt_in} minOut={min_out_val}")

        if self._is_duplicate("sell", token_in, token_out):
            self._log("[SELL] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            tx_hash = f"SIMULATED_SELL_{token_in}_{datetime.datetime.now().isoformat()}"
            self.open_positions_count = max(0, self.open_positions_count - 1)
            self._log(f"[DRY_RUN] Venda simulada: {tx_hash}")
            return tx_hash

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.sell_token(token_in, token_out, amt_in, min_out_val)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.open_positions_count = max(0, self.open_positions_count - 1)
            self._log(f"[SELL] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            self._log(f"[SELL] Falha ao executar venda: {e}")
            return None

    def stop(self):
        self._log("Executor encerrando ciclo.")
        if self.alert:
            try:
                self.alert.flush_report()
            except Exception as e:
                logger.error(f"Erro ao enviar relatório final do executor: {e}", exc_info=True)


RealTradeExecutor = TradeExecutor


class SafeTradeExecutor:
    """
    Envolve o TradeExecutor com checagens de risco e integra ao relatório consolidado.
    Aceita tanto amount_in_wei/amountoutmin quanto amountinwei/amount_out_min (compat).
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
        # Usa o alert passado ou herda do executor
        self.alert = alert or getattr(executor, "alert", None)

    def _log(self, msg: str):
        if self.alert:
            try:
                self.alert.log_event(msg)
            except Exception as e:
                logger.warning(f"Falha ao enviar para alert: {e}")
        logger.info(msg)

    def _trade_size_eth(self) -> float:
        # Compatibilidade: trade_size ou tradesize
        return getattr(self.executor, "trade_size", None) or getattr(self.executor, "tradesize", 0.0)

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
            self._log(f"[RISK] can_trade {kwargs} -> {allowed}")
            return allowed
        except Exception as e:
            self._log(f"[RISK] Erro em can_trade: {e}")
            return False

    def _register(self, sucesso: bool):
        try:
            self.risk.register_trade(success=sucesso)
            self._log(f"[RISK] Registro de trade: sucesso={sucesso}")
        except Exception as e:
            self._log(f"[RISK] Erro ao registrar trade: {e}")

    async def buy(
        self,
        path: list,
        amount_in_wei: Optional[int] = None,
        amountinwei: Optional[int] = None,
        amount_out_min: Optional[int] = None,
        amountoutmin: Optional[int] = None,
        current_price: float = 0.0,
        last_trade_price: float = 0.0
    ) -> Optional[str]:
        trade_sz = self._trade_size_eth()
        if not self._can_trade(current_price, last_trade_price, "buy", trade_sz):
            self._log("[SAFE BUY] Bloqueada pelo RiskManager")
            return None

        tx = await self.executor.buy(
            path=path,
            amount_in_wei=amount_in_wei,
            amountinwei=amountinwei,
            amount_out_min=amount_out_min,
            amountoutmin=amountoutmin
        )
        self._register(sucesso=(tx is not None))
        return tx

    async def sell(
        self,
        path: list,
        amount_in_wei: Optional[int] = None,
        amountinwei: Optional[int] = None,
        min_out: Optional[int] = None,
        minout: Optional[int] = None,
        current_price: float = 0.0,
        last_trade_price: float = 0.0
    ) -> Optional[str]:
        trade_sz = self._trade_size_eth()
        if not self._can_trade(current_price, last_trade_price, "sell", trade_sz):
            self._log("[SAFE SELL] Bloqueada pelo RiskManager")
            return None

        tx = await self.executor.sell(
            path=path,
            amount_in_wei=amount_in_wei,
            amountinwei=amountinwei,
            min_out=min_out,
            minout=minout
        )
        self._register(sucesso=(tx is not None))
        return tx

    def record_outcome(self, loss_eth: float = 0.0):
        if loss_eth <= 0:
            return
        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.register_loss(loss_eth)
                self._log(f"[RISK] Registrado prejuízo de {loss_eth} ETH")
        except Exception as e:
            self._log(f"[RISK] Erro ao registrar perda: {e}")

    def stop(self):
        self._log("[SAFE EXECUTOR] Encerrando ciclo.")
        if self.alert:
            try:
                self.alert.flush_report()
            except Exception as e:
                logger.error(f"Erro ao enviar relatório final do SAFE: {e}", exc_info=True)
