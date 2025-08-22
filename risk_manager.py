import logging
from decimal import Decimal
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# Config do Telegram — substitua pelos valores reais
BOT_TOKEN = "SEU_BOT_TOKEN"
CHAT_ID = "SEU_CHAT_ID"

def send_telegram(msg: str):
    """Envia mensagem simples para o Telegram."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
    except Exception as e:
        logger.error(f"[TELEGRAM] Falha ao enviar: {e}")

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

        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl_eth = Decimal("0")
        self.last_trade_time_by_pair = {}

        # Histórico de eventos
        self.eventos = []

        # 🆕 Armazena último motivo de bloqueio
        self.last_block_reason = None

    def _registrar_evento(
        self,
        tipo,
        mensagem,
        pair=None,
        direction=None,
        trade_size_eth=None,
        current_price=None,
        last_trade_price=None
    ):
        evento = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo,
            "mensagem": mensagem,
            "pair": pair,
            "direction": direction,
            "tamanho_eth": trade_size_eth,
            "preco_atual": current_price,
            "ultimo_preco": last_trade_price
        }
        self.eventos.append(evento)

        # 🆕 Atualiza último motivo de bloqueio
        if tipo == "bloqueio":
            self.last_block_reason = mensagem
        elif tipo == "liberado":
            self.last_block_reason = None

        # Logs locais
        log_line = f"{mensagem} | {evento}"
        if tipo == "bloqueio":
            logger.warning(log_line)
        elif tipo == "erro_trade":
            logger.error(log_line)
        else:
            logger.info(log_line)

        # Telegram
        try:
            send_telegram(f"📢 {mensagem}\n{evento}")
        except Exception as e:
            logger.error(f"[TELEGRAM] Falha ao enviar evento: {e}")

    def gerar_relatorio(self):
        """Gera relatório consolidado de eventos."""
        if not self.eventos:
            return "Nenhum evento registrado ainda."
        linhas = []
        for e in self.eventos:
            linhas.append(
                f"{e['timestamp']} | {e['tipo']} | {e.get('pair')} | {e.get('direction')} | "
                f"{e.get('tamanho_eth','-')} ETH | preço: {e.get('preco_atual')} | {e.get('mensagem')}"
            )
        relatorio = "\n".join(linhas)
        send_telegram(f"📊 Relatório:\n{relatorio}")
        return relatorio

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
        """Verifica se a trade é permitida e registra motivo de bloqueio, se houver."""
        if self.daily_trades >= self.max_trades_per_day:
            self._registrar_evento("bloqueio", "🚫 Limite diário de trades atingido",
                                   pair, direction, trade_size_eth, current_price, last_trade_price)
            return False

        if self.loss_streak >= self.loss_limit:
            self._registrar_evento("bloqueio", "🛑 Circuit breaker ativado (limite de perdas consecutivas)",
                                   pair, direction, trade_size_eth, current_price, last_trade_price)
            return False

        if self.realized_pnl_eth / self.capital <= -self.daily_loss_pct_limit:
            self._registrar_evento("bloqueio", "📉 Perda máxima diária atingida",
                                   pair, direction, trade_size_eth, current_price, last_trade_price)
            return False

        if trade_size_eth is not None:
            ts_eth = Decimal(str(trade_size_eth))
            if ts_eth > self.capital * self.max_exposure_pct:
                self._registrar_evento("bloqueio", f"💰 Trade {ts_eth} ETH excede exposição máxima",
                                       pair, direction, trade_size_eth, current_price, last_trade_price)
                return False

        if direction == "buy" and last_trade_price:
            if current_price > last_trade_price * 1.10:
                self._registrar_evento("bloqueio", "⚠️ Preço subiu >10% desde última compra",
                                       pair, direction, trade_size_eth, current_price, last_trade_price)
                return False

        if not min_liquidity_ok:
            self._registrar_evento("bloqueio", "💧 Liquidez insuficiente",
                                   pair, direction, trade_size_eth, current_price, last_trade_price)
            return False

        if not not_honeypot:
            self._registrar_evento("bloqueio", "🐝 Possível honeypot detectado",
                                   pair, direction, trade_size_eth, current_price, last_trade_price)
            return False

        if pair and now_ts:
            last_ts = self.last_trade_time_by_pair.get((pair[0], pair[1], direction))
            if last_ts and (now_ts - last_ts) < self.cooldown_sec:
                self._registrar_evento("bloqueio", f"⏳ Cooldown ativo {self.cooldown_sec}s",
                                       pair, direction, trade_size_eth, current_price, last_trade_price)
                return False

        # Trade liberada
        self._registrar_evento("liberado", "✅ Trade liberada",
                               pair, direction, trade_size_eth, current_price, last_trade_price)
        return True

    def register_trade(self, success=True, pair=None, direction=None, now_ts=None):
        """Registra execução de trade."""
        self.daily_trades += 1
        if success:
            self.loss_streak = 0
            self._registrar_evento("compra" if direction == "buy" else "venda",
                                   "✅ Trade executada com sucesso", pair, direction)
        else:
            self.loss_streak += 1
            self._registrar_evento("erro_trade", "❌ Falha na execução do trade",
                                   pair, direction)

        if pair and now_ts and direction:
            self.last_trade_time_by_pair[(pair[0], pair[1], direction)] = now_ts

    def register_pnl(self, pnl_eth: float):
        """Registra lucro/prejuízo realizado."""
        self.realized_pnl_eth += Decimal(str(pnl_eth))

    def reset_daily_limits(self):
        """Reseta todos os contadores e motivo de bloqueio."""
        self.daily_trades = 0
        self.loss_streak = 0
        self.realized_pnl_eth = Decimal("0")
        self.last_trade_time_by_pair.clear()
        self.last_block_reason = None
        self._registrar_evento("reset", "🔄 Limites diários resetados")
