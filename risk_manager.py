import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(
        self,
        capital_eth: float = 1.0,
        max_exposure_pct: float = 0.1,
        max_trades_per_day: int = 10,
        loss_limit: int = 3,
        daily_loss_pct_limit: float = 0.15,
        cooldown_sec: int = 30
    ):
        """
        capital_eth: capital total disponível em ETH
        max_exposure_pct: percentual máximo do capital em um trade (0.1 = 10%)
        daily_loss_pct_limit: perda máxima no dia (% do capital)
        cooldown_sec: tempo mínimo entre trades no mesmo par
        """
        self.capital = Decimal(str(capital_eth))
        self.max_exposure_pct = Decimal(str(max_exposure_pct))
        self.max_trades_per_day = max_trades_per_day
        self.loss_limit = loss_limit
        self.daily_loss_pct_limit = Decimal(str(daily_loss_pct_limit))
        self.cooldown_sec = cooldown_sec

        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl_eth = Decimal("0")
        self.last_trade_time_by_pair = {}  # {(token_in, token_out, side): timestamp}

    def can_trade(
        self,
        current_price,
        last_trade_price,
        direction,
        trade_size_eth=None,
        min_liquidity_ok=True,
        not_honeypot=True,
        pair=None,
        now_ts=None
    ):
        """Retorna True se trade for permitido, False caso contrário."""
        # 1️⃣ Limite diário de trades
        if self.daily_trades >= self.max_trades_per_day:
            logger.warning("🚫 Limite diário de trades atingido")
            return False

        # 2️⃣ Circuit breaker por sequência de perdas
        if self.loss_streak >= self.loss_limit:
            logger.warning("🛑 Circuit breaker: sequência de perdas")
            return False

        # 3️⃣ Perda acumulada no dia
        if self.realized_pnl_eth / self.capital <= -self.daily_loss_pct_limit:
            logger.warning("📉 Perda máxima diária atingida")
            return False

        # 4️⃣ Exposição máxima de capital
        if trade_size_eth is not None:
            ts_eth = Decimal(str(trade_size_eth))
            if ts_eth > self.capital * self.max_exposure_pct:
                logger.warning(f"💰 Trade {ts_eth} ETH excede exposição máxima permitida")
                return False

        # 5️⃣ Filtro de preço
        if direction == "buy" and last_trade_price:
            if current_price > last_trade_price * 1.10:
                logger.warning("⚠️ Preço subiu >10% desde última compra — bloqueando")
                return False

        # 6️⃣ Regras on-chain
        if not min_liquidity_ok:
            logger.warning("💧 Liquidez insuficiente — bloqueando trade")
            return False
        if not not_honeypot:
            logger.warning("🐝 Possível honeypot detectado — bloqueando trade")
            return False

        # 7️⃣ Cooldown entre trades no mesmo par
        if pair and now_ts:
            last_ts = self.last_trade_time_by_pair.get((pair[0], pair[1], direction))
            if last_ts and (now_ts - last_ts) < self.cooldown_sec:
                logger.warning(f"⏳ Cooldown ativo para par {pair} — aguarde {self.cooldown_sec}s")
                return False

        return True

    def register_trade(self, success=True, pair=None, direction=None, now_ts=None):
        self.daily_trades += 1
        if success:
            self.loss_streak = 0
        else:
            self.loss_streak += 1
        if pair and now_ts and direction:
            self.last_trade_time_by_pair[(pair[0], pair[1], direction)] = now_ts

    def register_pnl(self, pnl_eth: float):
        """Atualiza PnL acumulado no dia. pnl_eth positivo = ganho; negativo = perda."""
        self.realized_pnl_eth += Decimal(str(pnl_eth))

    def reset_daily_limits(self):
        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl_eth = Decimal("0")
        self.last_trade_time_by_pair.clear()
