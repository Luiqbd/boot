import logging
from decimal import Decimal
import time
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

BOT_TOKEN = "SEU_BOT_TOKEN"
CHAT_ID = "SEU_CHAT_ID"


def send_telegram(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg}
        )
    except Exception as e:
        logger.error(f"[TELEGRAM] Falha ao enviar: {e}")


class RiskManager:
    """
    Gerencia limites de trades, exposições e gera relatório de eventos,
    notificando via Telegram.
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
        self.eventos = []
        self.last_block_reason = None

    def _registrar_evento(
        self,
        tipo: str,
        mensagem: str,
        pair=None,
        direction=None,
        trade_size_eth=None,
        current_price=None,
        last_trade_price=None,
        min_liquidity_req=None,
        min_liquidity_found=None,
        slippage_allowed=None,
        slippage_found=None,
        spread=None,
        origem=None
    ):
        evento = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tipo": tipo,
            "mensagem": mensagem,
            "pair": pair,
            "direction": direction,
            "tamanho_eth": trade_size_eth,
            "preco_atual": current_price,
            "ultimo_preco": last_trade_price,
            "liq_min_exigida": min_liquidity_req,
            "liq_encontrada": min_liquidity_found,
            "slip_max": slippage_allowed,
            "slip_encontrado": slippage_found,
            "spread": spread,
            "origem": origem
        }
        self.eventos.append(evento)

        # Define ícone e registra último motivo de bloqueio/liberação
        if tipo == "bloqueio":
            self.last_block_reason = mensagem
            icone = "🚫"
        elif tipo == "liberado":
            self.last_block_reason = None
            icone = "✅"
        else:
            icone = "❌" if tipo == "erro_trade" else "📊"

        # Monta mensagem para Telegram
        msg_tg = (
            f"{icone} [{evento['timestamp']}] {tipo.upper()} {pair or ''} {direction or ''}\n"
            f"💰 {trade_size_eth or '-'} ETH\n"
            f"💹 Preço atual: {current_price or '-'} | Último: {last_trade_price or '-'}\n"
            f"💧 Liquidez: min {min_liquidity_req or '-'} / atual {min_liquidity_found or '-'}\n"
            f"📈 Slippage: max {slippage_allowed or '-'} / atual {slippage_found or '-'}\n"
            f"📏 Spread: {spread or '-'}\n"
            f"🛠 Origem: {origem or '-'}\n"
            f"📄 Motivo: {mensagem}"
        )
        send_telegram(msg_tg)

    def gerar_relatorio(self):
        """
        Retorna string com histórico de eventos invertido (mais recente primeiro)
        e envia relatório completo via Telegram.
        """
        if not self.eventos:
            return "Nenhum evento registrado ainda."

        linhas = []
        for e in reversed(self.eventos):
            linhas.append(
                f"{e['timestamp']} | {e['tipo'].upper()} | {e.get('pair','-')} | {e.get('direction','-')} | "
                f"{e.get('tamanho_eth','-')} ETH | Preço: {e.get('preco_atual','-')} | "
                f"Liq: {e.get('liq_encontrada','-')} (min {e.get('liq_min_exigida','-')}) | "
                f"Slip: {e.get('slip_encontrado','-')}/{e.get('slip_max','-')} | "
                f"Spread: {e.get('spread','-')} | Motivo: {e.get('mensagem')}"
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
        now_ts=None,
        min_liquidity_req=None,
        min_liquidity_found=None,
        slippage_allowed=None,
        slippage_found=None,
        spread=None
    ):
        origem = "can_trade"

        # Checa limite diário de trades
        if self.daily_trades >= self.max_trades_per_day:
            self._registrar_evento(
                "bloqueio", "Limite diário de trades atingido",
                pair, direction, trade_size_eth, current_price, last_trade_price,
                min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
            )
            return False

        # Circuit breaker de perdas consecutivas
        if self.loss_streak >= self.loss_limit:
            self._registrar_evento(
                "bloqueio", "Circuit breaker (perdas consecutivas)",
                pair, direction, trade_size_eth, current_price, last_trade_price,
                min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
            )
            return False

        # Limite de perda diária
        if self.realized_pnl_eth / self.capital <= -self.daily_loss_pct_limit:
            self._registrar_evento(
                "bloqueio", "Perda máxima diária atingida",
                pair, direction, trade_size_eth, current_price, last_trade_price,
                min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
            )
            return False

        # Exposição máxima por trade
        if trade_size_eth is not None:
            ts_eth = Decimal(str(trade_size_eth))
            if ts_eth > self.capital * self.max_exposure_pct:
                self._registrar_evento(
                    "bloqueio",
                    f"Trade {ts_eth} ETH excede exposição máxima ({self.max_exposure_pct * 100}%)",
                    pair, direction, trade_size_eth, current_price, last_trade_price,
                    min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
                )
                return False

        # Proteção contra pump excessivo antes de buy
        if direction == "buy" and last_trade_price:
            if current_price > last_trade_price * Decimal("1.10"):
                self._registrar_evento(
                    "bloqueio", "Preço subiu >10% desde última compra",
                    pair, direction, trade_size_eth, current_price, last_trade_price,
                    min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
                )
                return False

        # Verifica liquidez mínima
        if not min_liquidity_ok:
            self._registrar_evento(
                "bloqueio", "Liquidez insuficiente",
                pair, direction, trade_size_eth, current_price, last_trade_price,
                min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
            )
            return False

        # Verifica honeypot
        if not not_honeypot:
            self._registrar_evento(
                "bloqueio", "Possível honeypot detectado",
                pair, direction, trade_size_eth, current_price, last_trade_price,
                min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
            )
            return False

        # Cooldown por par e direção
        if pair and now_ts:
            last_ts = self.last_trade_time_by_pair.get((pair, direction))
            if last_ts and (now_ts - last_ts) < self.cooldown_sec:
                self._registrar_evento(
                    "bloqueio", f"Cooldown ativo ({self.cooldown_sec}s)",
                    pair, direction, trade_size_eth, current_price, last_trade_price,
                    min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
                )
                return False

        # Se passou em todos os filtros, libera
        self._registrar_evento(
            "liberado", "Trade liberada",
            pair, direction, trade_size_eth, current_price, last_trade_price,
            min_liquidity_req, min_liquidity_found, slippage_allowed, slippage_found, spread, origem
        )
        return True

    def register_trade(self, success: bool = True, pair=None, direction=None, now_ts: int = None):
        """
        Atualiza contadores após tentativa de trade e registra timestamp.
        """
        origem = "register_trade"
        self.daily_trades += 1

        if success:
            self.loss_streak = 0
        else:
            self.loss_streak += 1

        # Marca hora da última trade bem-sucedida/falha para cooldown
        if pair and direction:
            ts = now_ts or int(time.time())
            self.last_trade_time_by_pair[(pair, direction)] = ts
