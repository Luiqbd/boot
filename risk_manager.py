# risk_manager.py

import logging
import time
from decimal import Decimal
from threading import Lock
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import config
from utils import escape_md_v2, _notify

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
        self.capital = Decimal(str(capital_eth))
        self.max_exp = Decimal(str(max_exposure_pct))
        self.max_td  = max_trades_per_day
        self.loss_lim= loss_limit
        self.daily_loss_limit = Decimal(str(daily_loss_pct_limit))
        self.cooldown = cooldown_sec

        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl = Decimal("0")
        self.last_trade_time: Dict[Tuple[str,str],float] = {}
        self.events: List[Dict[str,Any]] = []
        self._lock = Lock()

    def record_evento(self, tipo: str, msg: str, detalhes: Dict[str,Any]):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            evt = {"timestamp": timestamp, "tipo": tipo, "mensagem": msg}
            evt.update(detalhes)
            self.events.append(evt)
        icon = {"bloqueio":"🚫","liberado":"✅"}.get(tipo, "📊")
        md = f"{icon} *{tipo.upper()}* `{timestamp}`\n"
        md += f"{escape_md_v2(msg)}\n"
        for k,v in detalhes.items():
            md += f"{k}: `{v}` | "
        md = md.rstrip(" | ")
        _notify(md, via_alert=True)

    def can_trade(
        self,
        current_price: Decimal,
        last_trade_price: Optional[Decimal],
        direction: str,
        amount_eth: Decimal
    ) -> bool:
        # Exposição
        if amount_eth > self.capital * self.max_exp:
            self.record_evento("bloqueio", "Exposição > limite", {"direction":direction})
            return False
        # Trades/dia
        if self.daily_trades >= self.max_td:
            self.record_evento("bloqueio", "Máximo trades/dia", {})
            return False
        # Cooldown
        now = time.time()
        key = (direction,)
        last = self.last_trade_time.get(key)
        if last and now - last < self.cooldown:
            self.record_evento("bloqueio", "Cooldown ativo", {"direction":direction})
            return False
        self.record_evento("liberado", "Trade permitida", {"direction":direction})
        return True

    def register_trade(self, success: bool, direction: str):
        with self._lock:
            self.daily_trades += 1
            if success:
                self.loss_streak = 0
            else:
                self.loss_streak += 1
            self.last_trade_time[(direction,)] = time.time()

    def gerar_relatorio(self) -> str:
        if not self.events:
            return "Nenhum evento."
        lines = []
        for evt in self.events[-20:]:
            lines.append(
                f"{evt['timestamp']} | {evt['tipo']} | {evt['mensagem']}"
            )
        rel = "📊 *Relatório de Risco*\n" + "\n".join(lines)
        _notify(rel, via_alert=True)
        return rel

risk_manager = RiskManager(
    capital_eth=float(config.get("CAPITAL_ETH",1.0)),
    max_exposure_pct=float(config.get("MAX_EXPOSURE_PCT",0.1)),
    max_trades_per_day=int(config.get("MAX_TRADES_PER_DAY",10)),
    loss_limit=int(config.get("LOSS_LIMIT",3)),
    daily_loss_pct_limit=float(config.get("DAILY_LOSS_PCT_LIMIT",0.15)),
    cooldown_sec=int(config.get("COOLDOWN_SEC",30))
)
