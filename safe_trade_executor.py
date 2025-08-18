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

    def _can_trade(self, current_price, last_trade_price, direction, amount_eth) -> bool:
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
            if "trade_size_eth" in params:
                kwargs["trade_size_eth"] = amount_eth
            elif "amount_eth" in params:
                kwargs["amount_eth"] = amount_eth

            allowed = self.risk.can_trade(**kwargs)
            logger.info(f"🔍 RiskManager.can_trade chamado com: {kwargs} → Resultado: {allowed}")
            return allowed
        except Exception as e:
            logger.error(f"❌ Erro ao consultar RiskManager.can_trade: {e}", exc_info=True)
            return False

    def _register_trade(self, success: bool):
        try:
            self.risk.register_trade(success=success)
        except Exception as e:
            logger.error(f"❌ Erro ao registrar trade no RiskManager: {e}", exc_info=True)

    def buy(self, token_in, token_out, amount_eth, current_price, last_trade_price, amount_out_min=None):
        """
        Tenta executar uma compra. Retorna tx_hash em caso de sucesso, ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "buy", amount_eth):
            logger.warning(f"🚫 Compra bloqueada pelo RiskManager | Preço: {current_price} | Tamanho: {amount_eth} ETH")
            return None

        logger.info(f"📤 Executando compra: {amount_eth} ETH → {token_out} | MinOut: {amount_out_min}")

        try:
            tx = self.executor.buy(token_in, token_out, amount_eth, amount_out_min=amount_out_min)
            logger.info(f"✅ Resultado da transação de compra: {tx}")
        except Exception as e:
            logger.error(f"❌ Erro inesperado no executor.buy: {e}", exc_info=True)
            self._register_trade(success=False)
            return None

        self._register_trade(success=tx is not None)
        return tx

    def sell(self, token_in, token_out, amount_eth, current_price, last_trade_price, amount_out_min=None):
        """
        Tenta executar uma venda. Retorna tx_hash em caso de sucesso, ou None se bloqueado/falha.
        """
        if not self._can_trade(current_price, last_trade_price, "sell", amount_eth):
            logger.warning(f"🚫 Venda bloqueada pelo RiskManager | Preço: {current_price} | Tamanho: {amount_eth} ETH")
            return None

        logger.info(f"📤 Executando venda: {amount_eth} ETH → {token_out} | MinOut: {amount_out_min}")

        try:
            tx = self.executor.sell(token_in, token_out, amount_eth, amount_out_min=amount_out_min)
            logger.info(f"✅ Resultado da transação de venda: {tx}")
        except Exception as e:
            logger.error(f"❌ Erro inesperado no executor.sell: {e}", exc_info=True)
            self._register_trade(success=False)
            return None

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
                logger.debug(f"📉 Registrado prejuízo de {loss_eth} ETH no RiskManager")
            else:
                logger.debug("ℹ️ RiskManager não possui register_loss; ignorando registro de perda.")
        except Exception as e:
            logger.error(f"❌ Erro ao registrar perda no RiskManager: {e}", exc_info=True)
