import logging

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self, capital=1.0, max_exposure_pct=0.1, max_trades_per_day=10, loss_limit=3):
        self.capital = capital
        self.max_exposure_pct = max_exposure_pct
        self.max_trades_per_day = max_trades_per_day
        self.loss_limit = loss_limit

        self.daily_trades = 0
        self.loss_streak = 0

    def can_trade(self, current_price, last_trade_price, direction):
        if self.daily_trades >= self.max_trades_per_day:
            logger.warning("🚫 Limite diário de trades atingido")
            return False

        if self.loss_streak >= self.loss_limit:
            logger.warning("🛑 Circuit breaker ativado — sequência de perdas")
            return False

        # Exemplo de lógica: evitar comprar se preço subiu muito
        if direction == "buy" and current_price > last_trade_price * 1.10:
            logger.warning("⚠️ Preço muito alto para nova compra")
            return False

        return True

    def register_trade(self, success=True):
        self.daily_trades += 1
        if success:
            self.loss_streak = 0
        else:
            self.loss_streak += 1

    def reset_daily_limits(self):
        self.daily_trades = 0
        self.loss_streak = 0
