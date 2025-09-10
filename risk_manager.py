# risk_manager.py

import logging
import time
from decimal import Decimal
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from datetime import datetime

from config import config
from utils import escape_md_v2, _notify  # Assumindo função _notify disponível em utils

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Gerencia política de risco do sniper:
      - limites de exposição e trades diários
      - registro de eventos e notificações
      - controle de cooldown por par e direção
      - geração de relatório de eventos
    """

    def __init__(
        self,
        capital_eth: float = 1.0,
        max_exposure_pct: float = 0.1,
        max_trades_per_day: int = 10,
        loss_limit: int = 3,
        daily_loss_pct_limit: float = 0.15,
        cooldown_sec: int = 30
    ):
        # Parâmetros de risco
        self.capital = Decimal(str(capital_eth))
        self.max_exposure_pct = Decimal(str(max_exposure_pct))
        self.max_trades_per_day = int(max_trades_per_day)
        self.loss_limit = int(loss_limit)
        self.daily_loss_pct_limit = Decimal(str(daily_loss_pct_limit))
        self.cooldown_sec = int(cooldown_sec)

        # Estado dinâmico
        self.daily_trades: int = 0
        self.loss_streak: int = 0
        self.realized_pnl_eth: Decimal = Decimal("0")
        self.last_trade_time_by_pair: Dict[Tuple[str, str], float] = {}
        self.eventos: List[Dict[str, Any]] = []
        self.last_block_reason: Optional[str] = None

        # Protege acesso concorrente a eventos
        self._lock = Lock()

    def record_evento(
        self,
        tipo: str,
        mensagem: str,
        detalhes: Dict[str, Any]
    ) -> None:
        """
        Registra um evento no histórico e notifica no Telegram.
        detalhes: campos variáveis (pair, direction, preços, slippage etc.)
        """
        with self._lock:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            evento = {"timestamp": timestamp, "tipo": tipo, "mensagem": mensagem}
            evento.update(detalhes)
            self.eventos.append(evento)

        # Define ícone conforme tipo
        if tipo == "bloqueio":
            icone = "🚫"
            self.last_block_reason = mensagem
        elif tipo == "liberado":
            icone = "✅"
            self.last_block_reason = None
        elif tipo in ("buy_failed", "sell_failed", "error"):
            icone = "❌"
        elif tipo in ("buy_success", "sell_success"):
            icone = "💰"
        else:
            icone = "📊"

        # Monta mensagem MarkdownV2
        md = f"{icone} *{tipo.upper()}* `{timestamp}`\n"
        md += f"Motivo: {mensagem}\n"
        for key, val in detalhes.items():
            md += f"{key}: `{val}` | "
        # Remove traço final
        md = md.rstrip(" | ")
        _notify(md, via_alert=True)

    def can_trade(
        self,
        pair: str,
        direction: str,
        trade_size_eth: Decimal,
        current_price: Decimal,
        last_trade_price: Optional[Decimal],
        min_liquidity_req: Decimal,
        min_liquidity_found: Decimal,
        slippage_allowed: Decimal,
        slippage_found: Decimal,
        spread: Decimal,
        not_honeypot: bool,
        now_ts: Optional[int] = None
    ) -> bool:
        """
        Retorna True se as condições de risco permitirem a operação.
        Em False, registra bloqueio com motivo.
        """
        # 1) Exposição máxima
        exposure = trade_size_eth
        limite = self.capital * self.max_exposure_pct
        if exposure > limite:
            msg = f"{exposure} ETH > exposição máxima {limite} ETH"
            self.record_evento("bloqueio", msg, {
                "pair": pair, "direction": direction,
                "trade_size_eth": exposure
            })
            return False

        # 2) Trade por dia
        if self.daily_trades >= self.max_trades_per_day:
            msg = f"Máximo de {self.max_trades_per_day} trades/dia atingido"
            self.record_evento("bloqueio", msg, {"pair": pair})
            return False

        # 3) Preço subiu >10% após última compra
        if direction == "buy" and last_trade_price:
            if current_price > last_trade_price * Decimal("1.10"):
                msg = f"Preço >10% acima do último ( {current_price}/{last_trade_price} )"
                self.record_evento("bloqueio", msg, {
                    "pair": pair, "current_price": current_price,
                    "last_trade_price": last_trade_price
                })
                return False

        # 4) Liquidez insuficiente
        if min_liquidity_found < min_liquidity_req:
            msg = f"Liquidez {min_liquidity_found} < exigida {min_liquidity_req}"
            self.record_evento("bloqueio", msg, {
                "pair": pair,
                "min_liquidity_req": min_liquidity_req,
                "min_liquidity_found": min_liquidity_found
            })
            return False

        # 5) Honeypot detectado
        if not not_honeypot:
            msg = "Possível honeypot"
            self.record_evento("bloqueio", msg, {"pair": pair})
            return False

        # 6) Cooldown por par/direção
        ts = now_ts or int(time.time())
        key = (pair, direction)
        last_ts = self.last_trade_time_by_pair.get(key)
        if last_ts and (ts - last_ts) < self.cooldown_sec:
            msg = f"Cooldown ativo ({self.cooldown_sec}s)"
            self.record_evento("bloqueio", msg, {"pair": pair, "direction": direction})
            return False

        # Tudo OK: libera trade
        self.record_evento("liberado", "Trade permitida", {
            "pair": pair, "direction": direction
        })
        return True

    def register_trade(
        self,
        success: bool,
        pair: Optional[str] = None,
        direction: Optional[str] = None,
        now_ts: Optional[int] = None
    ) -> None:
        """
        Registra contagem de trades e reset de streak.
        Deve ser chamado após buy/sell (sucesso ou falha).
        """
        with self._lock:
            self.daily_trades += 1
            if success:
                self.loss_streak = 0
            else:
                self.loss_streak += 1

            if pair and direction:
                ts = now_ts or int(time.time())
                self.last_trade_time_by_pair[(pair, direction)] = ts

    def register_loss(self, loss_eth: Decimal) -> None:
        """
        Registra prejuízo acumulado se disponível.
        """
        try:
            self.realized_pnl_eth -= loss_eth
        except Exception as e:
            logger.error(f"Erro ao registrar perda: {e}", exc_info=True)

    def gerar_relatorio(self) -> str:
        """
        Monta relatório textual dos últimos eventos e envia ao Telegram.
        """
        if not self.eventos:
            return "Nenhum evento registrado."

        lines: List[str] = []
        for evt in reversed(self.eventos[-50:]):  # últimos 50 eventos
            line = (
                f"{evt['timestamp']} | {evt['tipo'].upper()} | {evt.get('pair','-')} | "
                f"{evt.get('direction','-')} | {evt.get('mensagem','-')}"
            )
            lines.append(line)

        rel = "📊 *Relatório de Eventos*\n" + "\n".join(lines)
        _notify(rel, via_alert=True)
        return rel


# Instância global para importação direta
risk_manager = RiskManager(
    capital_eth=float(config.get("CAPITAL_ETH", 1.0)),
    max_exposure_pct=float(config.get("MAX_EXPOSURE_PCT", 0.1)),
    max_trades_per_day=int(config.get("MAX_TRADES_PER_DAY", 10)),
    loss_limit=int(config.get("LOSS_LIMIT", 3)),
    daily_loss_pct_limit=float(config.get("DAILY_LOSS_PCT_LIMIT", 0.15)),
    cooldown_sec=int(config.get("COOLDOWN_SEC", 30))
)
