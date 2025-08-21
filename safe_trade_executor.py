import inspect
import logging
from typing import Optional

from trade_executor import TradeExecutor
from risk_manager import RiskManager
from strategy_sniper import log_event, flush_report  # import correto ✅

logger = logging.getLogger(__name__)

class SafeTradeExecutor:
    def __init__(
        self,
        executor: TradeExecutor,
        max_trade_size_eth: float,
        slippage_bps: int,
        alert
    ):
        self.executor = executor
        self.risk = RiskManager(
            max_trade_size=max_trade_size_eth,
            slippage_bps=slippage_bps
        )
        self.alert = alert

    def _can_trade(self, current_price, last_trade_price, direction, amount_eth) -> bool:
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
            msg = f"RiskManager.can_trade({kwargs}) -> {allowed}"
            logger.debug(msg)
            self.alert.log_event(msg)
            return allowed
        except Exception as e:
            err = f"Erro em RiskManager.can_trade: {e}"
            logger.error(err, exc_info=True)
            self.alert.log_event(err)
            return False

    def _register(self, sucesso: bool):
        try:
            self.risk.register_trade(success=sucesso)
            self.alert.log_event(f"Trade {'registrado' if sucesso else 'falhou'} no RiskManager")
        except Exception as e:
            err = f"Erro ao registrar trade: {e}"
            logger.error(err, exc_info=True)
            self.alert.log_event(err)

    async def buy(self, path, amount_in_wei, amount_out_min, current_price, last_trade_price) -> Optional[str]:
        if not self._can_trade(current_price, last_trade_price, "buy", self.executor.tradesize):
            msg = "Compra bloqueada pelo RiskManager"
            logger.info(msg)
            self.alert.log_event(msg)
            return None

        tx = await self.executor.buy(path, amount_in_wei, amount_out_min)
        self._register(sucesso=(tx is not None))
        return tx

    async def sell(self, path, amount_in_wei, min_out, current_price, last_trade_price) -> Optional[str]:
        if not self._can_trade(current_price, last_trade_price, "sell", self.executor.tradesize):
            msg = "Venda bloqueada pelo RiskManager"
            logger.info(msg)
            self.alert.log_event(msg)
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
                msg = f"Registrado prejuízo de {loss_eth} ETH"
                logger.debug(msg)
                self.alert.log_event(msg)
        except Exception as e:
            err = f"Erro ao registrar perda: {e}"
            logger.error(err, exc_info=True)
            self.alert.log_event(err)

    def stop(self):
        try:
            self.alert.flush_report()
        except Exception as e:
            logger.error(f"Erro ao enviar relatório: {e}", exc_info=True)
