# risk_manager.py

import logging
from time import time
from datetime import datetime
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
        self.capital = Decimal(str(capital_eth))
        self.max_exposure_pct = Decimal(str(max_exposure_pct))
        self.max_trades_per_day = max_trades_per_day
        self.loss_limit = loss_limit
        self.daily_loss_pct_limit = Decimal(str(daily_loss_pct_limit))
        self.cooldown_sec = cooldown_sec

        # estados dinâmicos
        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl_eth = Decimal("0")
        self.last_trade_time_by_pair = {}
        self.events = []
        self.last_block_reason = None

    def record_event(self, event_type: str, **data):
        # timestamp e normalização
        ts = time()
        data["time"] = ts
        if "reason" in data:
            self.last_block_reason = data["reason"]
        self.events.append({"type": event_type, **data})

    def can_trade(
        self,
        current_price: Decimal,
        last_trade_price: Decimal | None,
        direction: str,
        trade_size_eth: Decimal,
        **kwargs
    ) -> bool:
        # exemplo de checagens, registrar bloqueios/liberação
        if self.daily_trades >= self.max_trades_per_day:
            self.record_event(
                "trade_blocked",
                reason="Limite diário atingido",
                direction=direction,
                trade_size_eth=float(trade_size_eth),
                current_price=float(current_price),
                **kwargs
            )
            return False
        # (outros checks...)
        self.record_event(
            "trade_allowed",
            reason="Trade dentro dos limites",
            direction=direction,
            trade_size_eth=float(trade_size_eth),
            current_price=float(current_price),
            **kwargs
        )
        return True

    def register_trade(
        self,
        success: bool,
        token: str,
        direction: str,
        trade_size_eth: float,
        entry_price: float,
        exit_price: float | None = None,
        tx_hash: str | None = None
    ):
        # chama após efetuar a trade, atualiza PnL e streak
        pnl = None
        if success and exit_price:
            pnl = Decimal(str(exit_price - entry_price)) * Decimal(str(trade_size_eth))
            self.realized_pnl_eth += pnl
            self.loss_streak = 0
        else:
            self.loss_streak += 1

        self.daily_trades += 1
        self.last_trade_time_by_pair[(token, direction)] = time()

        event_type = "trade_success" if success else "trade_failed"
        self.record_event(
            event_type,
            token=token,
            direction=direction,
            trade_size_eth=trade_size_eth,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=float(pnl) if pnl is not None else None,
            tx_hash=tx_hash
        )

    def generate_report(self, verbose: bool = False) -> str:
        # sumarização
        pairs_detected = len([e for e in self.events if e["type"] == "pair_detected"])
        buys = [e for e in self.events if e["type"] == "trade_success" and e["direction"] == "buy"]
        sells = [e for e in self.events if e["type"] == "trade_success" and e["direction"] == "sell"]
        blocks = [e for e in self.events if "blocked" in e["type"]]

        lines = [
            "📊 Relatório de Eventos",
            f"- Pares detectados: {pairs_detected}",
            f"- Trades bem-sucedidos: {len(buys) + len(sells)}",
            f"  • Compras: {len(buys)}",
            f"  • Vendas: {len(sells)}",
            f"- Trades bloqueados: {len(blocks)}",
            ""
        ]

        if blocks:
            lines.append("🛑 Bloqueios:")
            for e in blocks:
                t = datetime.fromtimestamp(e["time"]).strftime("%H:%M:%S")
                lines.append(f"  • [{t}] {e['direction']} {e.get('token')} → {e['reason']}")

        if buys or sells:
            lines.append("\n✅ Trades realizados:")
            for e in buys + sells:
                t = datetime.fromtimestamp(e["time"]).strftime("%H:%M:%S")
                lines.append(
                    f"  • [{t}] {e['direction']} {e['token']} | "
                    f"size {e['trade_size_eth']} ETH | pnl {e.get('pnl')} ETH | tx {e.get('tx_hash')}"
                )

        if verbose:
            lines.append("\n📋 Eventos detalhados:")
            for e in self.events:
                ts = datetime.fromtimestamp(e["time"]).isoformat()
                lines.append(f"{ts} | {e}")

        return "\n".join(lines)

# singleton
risk_manager = RiskManager()
