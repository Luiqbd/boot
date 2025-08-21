import inspect
import logging
from typing import Optional

from trade_executor import TradeExecutor
from risk_manager import RiskManager

logger = logging.getLogger(name)


class SafeTradeExecutor:
    """
    Envolve o TradeExecutor com checagens de risco.
    """

    def init(
        self,
        executor: TradeExecutor,
        maxtradesize_eth: float,
        slippage_bps: int
    ):
        self.executor = executor
        self.risk = RiskManager(
            maxtradesize=maxtradesize_eth,
            slippagebps=slippagebps
        )

    def cantrade(
        self,
        current_price: float,
        lasttradeprice: float,
        direction: str,
        amount_eth: float
    ) -> bool:
        try:
            sig = inspect.signature(self.risk.can_trade)
            params = sig.parameters
            kwargs = {}
            if "current_price" in params:
                kwargs["currentprice"] = currentprice
            if "lasttradeprice" in params:
                kwargs["lasttradeprice"] = lasttradeprice
            if "direction" in params:
                kwargs["direction"] = direction
            if "tradesizeeth" in params:
                kwargs["tradesizeeth"] = amount_eth
            elif "amount_eth" in params:
                kwargs["amounteth"] = amounteth

            allowed = self.risk.can_trade(kwargs)
            logger.debug(f"RiskManager.can_trade({kwargs}) -> {allowed}")
            return allowed
        except Exception as e:
            logger.error(f"Erro em RiskManager.cantrade: {e}", excinfo=True)
            return False

    def _register(self, sucesso: bool):
        try:
            self.risk.register_trade(success=sucesso)
        except Exception as e:
            logger.error(f"Erro ao registrar trade: {e}", exc_info=True)

    async def buy(
        self,
        path: list,
        amountinwei: int,
        amountoutmin: Optional[int],
        current_price: float,
        lasttradeprice: float
    ) -> Optional[str]:
        if not self.cantrade(currentprice, lasttradeprice, "buy", self.executor.tradesize):
            logger.info("Compra bloqueada pelo RiskManager")
            return None

        tx = await self.executor.buy(path, amountinwei, amountoutmin)
        self._register(sucesso=(tx is not None))
        return tx

    async def sell(
        self,
        path: list,
        amountinwei: int,
        min_out: Optional[int],
        current_price: float,
        lasttradeprice: float
    ) -> Optional[str]:
        if not self.cantrade(currentprice, lasttradeprice, "sell", self.executor.tradesize):
            logger.info("Venda bloqueada pelo RiskManager")
            return None

        tx = await self.executor.sell(path, amountinwei, min_out)
        self._register(sucesso=(tx is not None))
        return tx

    def recordoutcome(self, losseth: float = 0.0):
        if loss_eth <= 0:
            return
        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.registerloss(losseth)
                logger.debug(f"Registrado prejuízo de {loss_eth} ETH")
        except Exception as e:
            logger.error(f"Erro ao registrar perda: {e}", exc_info=True)
