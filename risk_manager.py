# risk_manager.py

import logging
import time
from decimal import Decimal
from threading import Lock
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from config import config
from utils import escape_md_v2

logger = logging.getLogger(__name__)

class RiskLevel(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

@dataclass
class TradeMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: Decimal = Decimal('0')
    total_loss: Decimal = Decimal('0')
    max_drawdown: Decimal = Decimal('0')
    current_drawdown: Decimal = Decimal('0')
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0

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
        self.capital = Decimal(str(capital_eth))
        self.max_exp = Decimal(str(max_exposure_pct))
        self.max_td  = max_trades_per_day
        self.loss_lim= loss_limit
        self.daily_loss_limit = Decimal(str(daily_loss_pct_limit))
        self.cooldown = cooldown_sec

        # Enhanced tracking
        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl = Decimal("0")
        self.last_trade_time: Dict[Tuple[str,str],float] = {}
        self.events: List[Dict[str,Any]] = []
        self.trade_history: List[Dict[str,Any]] = []
        self.metrics = TradeMetrics()
        self.current_risk_level = RiskLevel.LOW
        self.last_block_reason: Optional[str] = None
        self._lock = Lock()
        
        # Daily reset tracking
        self.last_reset_date = datetime.now().date()
        
        # Position tracking
        self.active_positions: Dict[str, Dict] = {}
        self.total_exposure = Decimal('0')
        
        # Performance tracking
        self.daily_pnl: Dict[str, Decimal] = {}
        self.hourly_trades: Dict[int, int] = {}
        
        # Risk thresholds
        self.max_consecutive_losses = 5
        self.max_daily_drawdown = Decimal('0.20')  # 20%
        self.volatility_threshold = 0.15

    def _check_daily_reset(self):
        """Check if we need to reset daily counters"""
        current_date = datetime.now().date()
        if current_date != self.last_reset_date:
            self.daily_trades = 0
            self.loss_streak = 0
            self.last_reset_date = current_date
            logger.info("Daily counters reset")
    
    def record(self, event_type: str, message: str, pair: str = None, token: str = None, 
              phase: str = None, tx_hash: str = None, dry_run: bool = True):
        """Enhanced event recording with more context"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        event = {
            "timestamp": timestamp,
            "type": event_type,
            "message": message,
            "pair": pair,
            "token": token,
            "phase": phase,
            "tx_hash": tx_hash,
            "dry_run": dry_run
        }
        
        with self._lock:
            self.events.append(event)
            # Keep only last 1000 events
            if len(self.events) > 1000:
                self.events = self.events[-1000:]
        
        # Update risk level based on event type
        self._update_risk_level(event_type)
        
        logger.info(f"Risk event: {event_type} - {message}")
    
    def _update_risk_level(self, event_type: str):
        """Update current risk level based on recent events"""
        if event_type in ["buy_failed", "sell_failed", "error"]:
            if self.loss_streak >= self.max_consecutive_losses:
                self.current_risk_level = RiskLevel.CRITICAL
            elif self.loss_streak >= 3:
                self.current_risk_level = RiskLevel.HIGH
            elif self.loss_streak >= 2:
                self.current_risk_level = RiskLevel.MEDIUM
        elif event_type in ["buy_success", "sell_success"]:
            if self.current_risk_level == RiskLevel.CRITICAL and self.loss_streak < 2:
                self.current_risk_level = RiskLevel.HIGH
            elif self.current_risk_level == RiskLevel.HIGH and self.loss_streak == 0:
                self.current_risk_level = RiskLevel.MEDIUM
            elif self.current_risk_level == RiskLevel.MEDIUM and self.loss_streak == 0:
                self.current_risk_level = RiskLevel.LOW

    def can_trade(
        self,
        current_price: Decimal,
        last_trade_price: Optional[Decimal],
        direction: str,
        amount_eth: Decimal,
        token: str = None,
        pair: str = None
    ) -> bool:
        """Enhanced trade validation with multiple risk checks"""
        self._check_daily_reset()
        
        # Critical risk level check
        if self.current_risk_level == RiskLevel.CRITICAL:
            self.last_block_reason = "Sistema em nível de risco crítico"
            self.record("trade_blocked", self.last_block_reason, pair, token, "risk_check")
            return False
        
        # Exposure check
        if amount_eth > self.capital * self.max_exp:
            self.last_block_reason = f"Exposição {amount_eth} > limite {self.capital * self.max_exp}"
            self.record("trade_blocked", self.last_block_reason, pair, token, "exposure_check")
            return False
        
        # Total exposure check
        if self.total_exposure + amount_eth > self.capital * Decimal('0.5'):  # Max 50% total exposure
            self.last_block_reason = f"Exposição total excederia 50% do capital"
            self.record("trade_blocked", self.last_block_reason, pair, token, "total_exposure_check")
            return False
        
        # Daily trades limit
        if self.daily_trades >= self.max_td:
            self.last_block_reason = f"Limite diário de trades atingido ({self.max_td})"
            self.record("trade_blocked", self.last_block_reason, pair, token, "daily_limit_check")
            return False
        
        # Consecutive losses check
        if self.loss_streak >= self.max_consecutive_losses:
            self.last_block_reason = f"Muitas perdas consecutivas ({self.loss_streak})"
            self.record("trade_blocked", self.last_block_reason, pair, token, "loss_streak_check")
            return False
        
        # Daily drawdown check
        today = datetime.now().strftime("%Y-%m-%d")
        daily_pnl = self.daily_pnl.get(today, Decimal('0'))
        if daily_pnl < -self.max_daily_drawdown * self.capital:
            self.last_block_reason = f"Drawdown diário excedido: {daily_pnl}"
            self.record("trade_blocked", self.last_block_reason, pair, token, "drawdown_check")
            return False
        
        # Cooldown check
        now = time.time()
        key = (direction, token or "")
        last = self.last_trade_time.get(key)
        if last and now - last < self.cooldown:
            remaining = int(self.cooldown - (now - last))
            self.last_block_reason = f"Cooldown ativo ({remaining}s restantes)"
            self.record("trade_blocked", self.last_block_reason, pair, token, "cooldown_check")
            return False
        
        # High risk level additional checks
        if self.current_risk_level == RiskLevel.HIGH:
            # Reduce position size in high risk
            if amount_eth > self.capital * self.max_exp * Decimal('0.5'):
                self.last_block_reason = "Posição reduzida devido ao alto risco"
                self.record("trade_blocked", self.last_block_reason, pair, token, "high_risk_check")
                return False
        
        # All checks passed
        self.record("trade_approved", f"Trade aprovado: {direction} {amount_eth} ETH", pair, token, "validation")
        return True

    def register_trade(self, success: bool, pair: str, direction: str, timestamp: int, 
                      pnl: Decimal = Decimal('0'), token: str = None):
        """Enhanced trade registration with detailed tracking"""
        with self._lock:
            self.daily_trades += 1
            
            # Update streak
            if success:
                self.loss_streak = 0
                self.metrics.winning_trades += 1
                self.metrics.total_profit += pnl
            else:
                self.loss_streak += 1
                self.metrics.losing_trades += 1
                self.metrics.total_loss += abs(pnl)
            
            # Update metrics
            self.metrics.total_trades += 1
            self.realized_pnl += pnl
            
            # Update daily PnL
            today = datetime.now().strftime("%Y-%m-%d")
            if today not in self.daily_pnl:
                self.daily_pnl[today] = Decimal('0')
            self.daily_pnl[today] += pnl
            
            # Update trade history
            trade_record = {
                "timestamp": timestamp,
                "pair": pair,
                "token": token,
                "direction": direction,
                "success": success,
                "pnl": float(pnl),
                "cumulative_pnl": float(self.realized_pnl)
            }
            self.trade_history.append(trade_record)
            
            # Keep only last 1000 trades
            if len(self.trade_history) > 1000:
                self.trade_history = self.trade_history[-1000:]
            
            # Update last trade time
            self.last_trade_time[(direction, token or "")] = time.time()
            
            # Calculate win rate
            if self.metrics.total_trades > 0:
                self.metrics.win_rate = self.metrics.winning_trades / self.metrics.total_trades
            
            # Calculate profit factor
            if self.metrics.total_loss > 0:
                self.metrics.profit_factor = float(self.metrics.total_profit / self.metrics.total_loss)
            
            # Update drawdown
            if pnl < 0:
                self.metrics.current_drawdown += abs(pnl)
                if self.metrics.current_drawdown > self.metrics.max_drawdown:
                    self.metrics.max_drawdown = self.metrics.current_drawdown
            else:
                self.metrics.current_drawdown = max(Decimal('0'), self.metrics.current_drawdown - pnl)
    
    def get_trade_count_24h(self) -> int:
        """Get trade count in last 24 hours"""
        cutoff = time.time() - 86400  # 24 hours ago
        return len([t for t in self.trade_history if t["timestamp"] > cutoff])
    
    def get_success_count_24h(self) -> int:
        """Get successful trades in last 24 hours"""
        cutoff = time.time() - 86400
        return len([t for t in self.trade_history if t["timestamp"] > cutoff and t["success"]])
    
    def get_failure_count_24h(self) -> int:
        """Get failed trades in last 24 hours"""
        cutoff = time.time() - 86400
        return len([t for t in self.trade_history if t["timestamp"] > cutoff and not t["success"]])
    
    def get_pnl_24h(self) -> Decimal:
        """Get PnL in last 24 hours"""
        cutoff = time.time() - 86400
        recent_trades = [t for t in self.trade_history if t["timestamp"] > cutoff]
        return sum(Decimal(str(t["pnl"])) for t in recent_trades)
    
    def gerar_relatorio(self) -> str:
        """Generate comprehensive risk report"""
        if not self.events and not self.trade_history:
            return "📊 *Relatório de Risco*\n\nNenhum dado disponível."
        
        # Performance metrics
        win_rate = self.metrics.win_rate * 100
        profit_factor = self.metrics.profit_factor
        
        # Recent performance
        pnl_24h = self.get_pnl_24h()
        trades_24h = self.get_trade_count_24h()
        success_24h = self.get_success_count_24h()
        
        report = f"""📊 *Relatório de Risco Avançado*

🎯 *Performance Geral:*
• Total Trades: `{self.metrics.total_trades}`
• Taxa de Acerto: `{win_rate:.1f}%`
• Profit Factor: `{profit_factor:.2f}`
• PnL Total: `{self.realized_pnl:.4f}` ETH
• Max Drawdown: `{self.metrics.max_drawdown:.4f}` ETH

📈 *Últimas 24h:*
• Trades: `{trades_24h}`
• Sucessos: `{success_24h}`
• PnL: `{pnl_24h:.4f}` ETH

⚠️ *Status de Risco:*
• Nível Atual: `{self.current_risk_level.name}`
• Sequência de Perdas: `{self.loss_streak}`
• Trades Hoje: `{self.daily_trades}/{self.max_td}`
• Exposição Total: `{self.total_exposure:.4f}` ETH

🔍 *Últimos Eventos:*"""
        
        # Add recent events
        recent_events = self.events[-10:] if self.events else []
        for event in recent_events:
            event_type = event.get("type", "unknown")
            message = event.get("message", "")
            timestamp = event.get("timestamp", "")
            
            emoji = {
                "trade_approved": "✅",
                "trade_blocked": "🚫", 
                "buy_success": "💰",
                "sell_success": "💸",
                "buy_failed": "❌",
                "sell_failed": "❌",
                "error": "⚠️"
            }.get(event_type, "📊")
            
            report += f"\n{emoji} `{timestamp}` - {message}"
        
        return report

risk_manager = RiskManager(
    capital_eth=float(config.get("CAPITAL_ETH",1.0)),
    max_exposure_pct=float(config.get("MAX_EXPOSURE_PCT",0.1)),
    max_trades_per_day=int(config.get("MAX_TRADES_PER_DAY",10)),
    loss_limit=int(config.get("LOSS_LIMIT",3)),
    daily_loss_pct_limit=float(config.get("DAILY_LOSS_PCT_LIMIT",0.15)),
    cooldown_sec=int(config.get("COOLDOWN_SEC",30))
)
