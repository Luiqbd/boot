# risk_manager.py

import os
import logging
from decimal import Decimal
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# Credenciais Telegram via variáveis de ambiente
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("CHAT_ID", "").strip()


def send_telegram(msg: str):
    """
    Envia mensagem via Bot Telegram se BOT_TOKEN e CHAT_ID estiverem configurados.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("[TELEGRAM] credenciais não configuradas. Pulei o envio.")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception as e:
        logger.error(f"[TELEGRAM] Falha ao enviar: {e}", exc_info=True)


class RiskManager:
    """
    Gerencia limites de risco: número de trades, exposição, drawdown, slippage, honeypot, cooldown.
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
        self.capital               = Decimal(str(capital_eth))
        self.max_exposure_pct      = Decimal(str(max_exposure_pct))
        self.max_trades_per_day    = max_trades_per_day
        self.loss_limit            = loss_limit
        self.daily_loss_pct_limit  = Decimal(str(daily_loss_pct_limit))
        self.cooldown_sec          = cooldown_sec

        self.daily_trades            = 0
        self.loss_streak             = 0
        self.realized_pnl_eth        = Decimal("0")
        self.last_trade_time_by_pair = {}
        self.eventos                 = []
        self.last_block_reason       = None

    def _registrar_evento(
        self,
        tipo,
        mensagem,
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

        if tipo == "bloqueio":
            self.last_block_reason = mensagem
        elif tipo == "liberado":
            self.last_block_reason = None

        icone = {
            "bloqueio":       "🚫",
            "liberado":       "✅",
            "erro_trade":     "❌",
            "sucesso_trade":  "📈",
            "trade_perdido":  "📉"
        }.get(tipo, "📊")

        msg_tg = (
            f"{icone} [{evento['timestamp']}] {tipo.upper()} {pair or ''} {direction or ''}\n"
            f"🔍 Origem: {origem}\n"
            f"📝 Motivo: {mensagem}"
        )
        send_telegram(msg_tg)

    def gerar_relatorio(self) -> str:
        if not self.eventos:
            return ""
        linhas = []
        for e in reversed(self.eventos):
            linhas.append(
                f"{e['timestamp']} | {e['tipo'].upper():12} | "
                f"{e.get('pair') or '-':42} | {e.get('direction') or '-':4} | "
                f"{e.get('tamanho_eth') or '-':>6} ETH | "
                f"Preço: {e.get('preco_atual') or '-'} | "
                f"Liq: {e.get('liq_encontrada') or '-'} (min {e.get('liq_min_exigida') or '-'}) | "
                f"Slip: {e.get('slip_encontrado') or '-'}/{e.get('slip_max') or '-'} | "
                f"Spread: {e.get('spread') or '-'} | "
                f"Motivo: {e.get('mensagem')}"
            )
        texto = "\n".join(linhas)
        send_telegram(f"📊 Relatório completo:\n{texto}")
        return texto

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
    ) -> bool:
        origem = "can_trade"
        # ... suas regras de risco aqui ...
        raise NotImplementedError("Defina as regras em can_trade() conforme sua lógica.")

    def register_trade(
        self,
        success: bool = True,
        pnl_eth: float = 0.0,
        pair=None,
        direction=None,
        now_ts=None
    ):
        origem = "register_trade"
        self.daily_trades += 1

        if pair and direction and now_ts is not None:
            key = (pair[0], pair[1], direction)
            self.last_trade_time_by_pair[key] = now_ts

        pnl = Decimal(str(pnl_eth))
        if success:
            self.realized_pnl_eth += pnl
            self.loss_streak = 0
            tipo = "sucesso_trade"
            msg   = f"Trade executado com lucro de {pnl} ETH"
        else:
            self.realized_pnl_eth -= abs(pnl)
            self.loss_streak += 1
            tipo = "trade_perdido"
            msg   = f"Trade com prejuízo de {abs(pnl)} ETH"

        self._registrar_evento(tipo, msg, pair, direction, None, None, None,
                               None, None, None, None, origem)

    def get_telato(self, url: str) -> dict:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"[RiskManager.get_telato] Falha ao buscar {url}: {e}")
            return {}

# Instância global para importação direta
risk_manager = RiskManager()
