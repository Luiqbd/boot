import logging
import inspect

logger = logging.getLogger(__name__)

class SafeTradeExecutor:
    """
    Envolve o TradeExecutor com checagens do RiskManager antes de executar ordens.
    Compatível com diferentes assinaturas de RiskManager.can_trade().
    """

    def __init__(self, executor, risk_manager):
        self.executor = executor
        self.risk = risk_manager

    def _can_trade(self, current_price, last_trade_price, direction, amount_eth):
        """
        Chama RiskManager.can_trade com compatibilidade:
        - Suporta versões com e sem 'trade_size_eth'/'amount_eth'.
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
            # Tenta nomes comuns para tamanho do trade
            if "trade_size_eth" in params:
                kwargs["trade_size_eth"] = amount_eth
            elif "amount_eth" in params:
                kwargs["amount_eth"] = amount_eth

            return self.risk.can_trade(**kwargs)
        except Exception as e:
            logger.error(f"Erro ao consultar RiskManager.can_trade: {e}")
            return False

    def _register_trade(self, success: bool):
        try:
            self.risk.register_trade(success=success)
        except Exception as e:
            logger.error(f"Erro ao registrar trade no RiskManager: {e}")

    def buy(self, token_in, token_out, amount_eth, current_price, last_trade_price):
        """
        Tenta executar uma compra. Retorna tx_hash em caso de sucesso, ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "buy", amount_eth):
            logger.info("Compra bloqueada pelo RiskManager")
            return None

        tx = self.executor.buy(token_in, token_out, amount_eth)
        self._register_trade(success=tx is not None)
        return tx

    def sell(self, token_in, token_out, amount_eth, current_price, last_trade_price):
        """
        Tenta executar uma venda. Retorna tx_hash em caso de sucesso, ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "sell", amount_eth):
            logger.info("Venda bloqueada pelo RiskManager")
            return None

        tx = self.executor.sell(token_in, token_out, amount_eth)
        self._register_trade(success=tx is not None)
        return tx

    def record_outcome(self, loss_eth: float = 0.0):
        """
        Opcional: chame após fechar posição para registrar prejuízo no RiskManager,
        caso ele possua register_loss(loss_eth).
        """
        if loss_eth <= 0:
            return
        try:
            if hasattr(self.risk, "register_loss"):
                self.risk.register_loss(loss_eth)
            else:
                logger.debug("RiskManager não possui register_loss; ignorando registro de perda.")
        except Exception as e:
            logger.error(f"Erro ao registrar perda no RiskManager: {e}")
