import logging
import inspect

from telegram import Bot
from config import config
from telegram_alert import send_report

logger = logging.getLogger(__name__)

# Bot de notificações centralizado
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])


class SafeTradeExecutor:
    """
    Envolve o TradeExecutor com checagens do RiskManager antes de executar ordens
    e notifica falhas de transferência via Telegram.
    Compatível com diferentes assinaturas de RiskManager.can_trade().
    """

    def __init__(self, executor, risk_manager):
        self.executor = executor
        self.risk = risk_manager

    def _can_trade(self, current_price, last_trade_price, direction, amount_eth) -> bool:
        """
        Chama RiskManager.can_trade com compatibilidade de assinatura.
        """
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
            logger.debug(f"RiskManager.can_trade({kwargs}) -> {allowed}")
            return allowed

        except Exception as e:
            logger.error(f"Erro ao consultar RiskManager.can_trade: {e}", exc_info=True)
            return False

    def _register_trade(self, success: bool):
        """
        Registra o resultado no RiskManager, se suportado.
        """
        try:
            self.risk.register_trade(success=success)
        except Exception as e:
            logger.error(f"Erro ao registrar trade no RiskManager: {e}", exc_info=True)

    def buy(
        self,
        token_in,
        token_out,
        amount_eth,
        current_price,
        last_trade_price,
        amount_out_min=None,
    ):
        """
        Tenta executar uma compra. Retorna tx_hash ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "buy", amount_eth):
            logger.info("Compra bloqueada pelo RiskManager")
            return None

        try:
            tx_hash = self.executor.buy(
                token_in,
                token_out,
                amount_eth,
                amount_out_min=amount_out_min,
            )
            self._register_trade(success=tx_hash is not None)
            return tx_hash

        except ValueError as ve:
            # Saldo insuficiente levantado pelo send_tx.send_eth
            msg = f"⚠️ Compra abortada: {ve}"
            logger.warning(msg)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

        except RuntimeError as re:
            # Erro genérico na transferência
            msg = f"❌ Erro na compra: {re}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

        except Exception as e:
            # Qualquer outro erro inesperado
            msg = f"❌ Erro inesperado no buy: {e}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

    def sell(
        self,
        token_in,
        token_out,
        amount_eth,
        current_price,
        last_trade_price,
        amount_out_min=None,
    ):
        """
        Tenta executar uma venda. Retorna tx_hash ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "sell", amount_eth):
            logger.info("Venda bloqueada pelo RiskManager")
            return None

        try:
            tx_hash = self.executor.sell(
                token_in,
                token_out,
                amount_eth,
                amount_out_min=amount_out_min,
            )
            self._register_trade(success=tx_hash is not None)
            return tx_hash

        except ValueError as ve:
            # Saldo insuficiente levantado pelo send_tx.send_eth
            msg = f"⚠️ Venda abortada: {ve}"
            logger.warning(msg)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

        except RuntimeError as re:
            # Erro genérico na transferência
            msg = f"❌ Erro na venda: {re}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

        except Exception as e:
            # Qualquer outro erro inesperado
            msg = f"❌ Erro inesperado no sell: {e}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            self._register_trade(success=False)
            return None

    def record_outcome(self, loss_eth: float = 0.0):
        """
        Opcional: registra prejuízo no RiskManager, se suportado.
        """
        if loss_eth <= 0:
            return
        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.register_loss(loss_eth)
                logger.debug(f"Registrado prejuízo de {loss_eth} ETH no RiskManager")
            else:
                logger.debug("RiskManager não possui register_loss; ignorando.")
        except Exception as e:
            logger.error(f"Erro ao registrar perda no RiskManager: {e}", exc_info=True)
