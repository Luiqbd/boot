# utils.py

import os
import re
import time
import logging
import requests
from collections import deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Callable, Deque, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

try:
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput, ABIFunctionNotFound, ContractLogicError
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    logger.warning("Web3 não disponível - funcionalidades blockchain limitadas")

from config import config

# -------------------------------------------------------------------
# Notificações Telegram (via Bot API)
# -------------------------------------------------------------------
def _notify(text: str, via_alert: bool = False) -> None:
    """
    Envia mensagem ao Telegram via Bot API, usando MarkdownV2.
    via_alert está reservado para futuros usos (por exemplo, TelegramAlert).
    """
    token   = config["TELEGRAM_TOKEN"]
    chat_id = config["TELEGRAM_CHAT_ID"]
    url     = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "MarkdownV2"
    }
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        logger.error(f"Falha ao enviar Telegram: {e}", exc_info=True)


def escape_md_v2(text: str) -> str:
    """
    Escapa caracteres especiais para MarkdownV2:
    _ * [ ] ( ) ~ ` > # + - = | { } . ! \
    """
    # Inclui o backslash no padrão para escapá-lo também
    pattern = r'([_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!\\])'
    return re.sub(pattern, r'\\\1', text)


# -------------------------------------------------------------------
# Rate Limiter para APIs externas
# -------------------------------------------------------------------
class ApiRateLimiter:
    """
    Limita chamadas de API por QPS e total diário, emitindo avisos via callback.
    """

    def __init__(
        self,
        qps_limit: int = 5,
        daily_limit: int = 100000,
        warn_pct: float = 0.85,
        pause_daily_pct: float = 0.95,
        qps_cooldown_sec: int = 10,
        daily_cooldown_sec: int = 3600,
        pause_enabled: bool = True
    ):
        self.qps_limit         = qps_limit
        self.daily_limit       = daily_limit
        self.warn_pct          = warn_pct
        self.pause_daily_pct   = pause_daily_pct
        self.qps_cd            = qps_cooldown_sec
        self.daily_cd          = daily_cooldown_sec
        self.pause_enabled     = pause_enabled

        self.calls_window: Deque[datetime] = deque()
        self.daily_count      = 0
        self.day_anchor       = self._today_utc()
        self.paused_until: Optional[datetime] = None
        self._notifier        = None
        self._warned_qps      = False
        self._warned_daily    = False

    def _today_utc(self) -> datetime:
        now = datetime.now(timezone.utc)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _reset_daily_if_needed(self) -> None:
        now = datetime.now(timezone.utc)
        if now >= self.day_anchor + timedelta(days=1):
            self.day_anchor    = self._today_utc()
            self.daily_count   = 0
            self._warned_daily = False
            self._notify("🔁 Limite diário de API resetado (novo dia).")

    def set_notifier(self, notifier: Callable[[str], None]) -> None:
        """
        Define callback(msg: str) para avisos do rate limiter.
        """
        self._notifier = notifier

    def _notify(self, msg: str) -> None:
        try:
            if self._notifier:
                self._notifier(msg)
            else:
                logger.info(f"[RATE LIMITER] {msg}")
        except Exception:
            logger.warning("Falha em notifier do rate limiter.", exc_info=True)

    def is_paused(self) -> bool:
        """
        Retorna True se atualmente em pausa por limite de API.
        """
        self._reset_daily_if_needed()
        if not self.paused_until:
            return False
        now = datetime.now(timezone.utc)
        if now >= self.paused_until:
            self.paused_until = None
            self._notify("▶️ Retomando após pausa de API.")
            return False
        return True

    def before_api_call(self) -> None:
        """
        Deve ser chamado antes de cada request a APIs externas.
        Lança RuntimeError se exceder QPS ou limite diário.
        """
        self._reset_daily_if_needed()
        if self.is_paused():
            raise RuntimeError("API rate-limited: paused")

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=1)
        while self.calls_window and self.calls_window[0] < window_start:
            self.calls_window.popleft()

        # Aviso de QPS
        if not self._warned_qps and len(self.calls_window) >= int(self.qps_limit * self.warn_pct):
            self._warned_qps = True
            self._notify(f"⚠️ Aproximando do limite de QPS ({len(self.calls_window)}/{self.qps_limit}/s).")

        if len(self.calls_window) >= self.qps_limit:
            if self.pause_enabled:
                self.paused_until = now + timedelta(seconds=self.qps_cd)
                self._notify(f"⏸️ Pausa QPS ({self.qps_cd}s): QPS atingido ({self.qps_limit}/s).")
            raise RuntimeError("API rate-limited: QPS exceeded")

        self.calls_window.append(now)
        self.daily_count += 1

        # Aviso diário
        if not self._warned_daily and self.daily_count >= int(self.daily_limit * self.warn_pct):
            self._warned_daily = True
            self._notify(f"⚠️ Aproximando do limite diário ({self.daily_count}/{self.daily_limit}).")

        if self.daily_count >= int(self.daily_limit * self.pause_daily_pct):
            if self.pause_enabled:
                until = min(
                    now + timedelta(seconds=self.daily_cd),
                    self.day_anchor + timedelta(days=1)
                )
                restante = int((until - now).total_seconds())
                self.paused_until = until
                self._notify(
                    f"⏸️ Pausa diária ({restante}s): consumo alto ({self.daily_count}/{self.daily_limit})."
                )
            raise RuntimeError("API rate-limited: daily threshold reached")


# Instância global
rate_limiter = ApiRateLimiter()


# -------------------------------------------------------------------
# Funções de Token e Contrato
# -------------------------------------------------------------------

def get_token_balance(token_address: str, wallet_address: str) -> float:
    """
    Obtém o saldo de um token específico para uma carteira.
    Retorna 0.0 em caso de erro.
    """
    try:
        # Implementação simplificada - retorna 0 por enquanto
        # Em produção, usaria Web3 para consultar o contrato ERC20
        logger.info(f"Consultando saldo do token {token_address} para {wallet_address}")
        return 0.0
    except Exception as e:
        logger.error(f"Erro ao obter saldo do token: {e}")
        return 0.0


def has_high_tax(token_address: str) -> bool:
    """
    Verifica se um token tem taxa alta (honeypot).
    Retorna False por padrão (implementação simplificada).
    """
    try:
        # Implementação simplificada - sempre retorna False
        # Em produção, usaria APIs como honeypot.is ou análise de contrato
        logger.info(f"Verificando taxa do token {token_address}")
        return False
    except Exception as e:
        logger.error(f"Erro ao verificar taxa do token: {e}")
        return False


def is_contract_verified(contract_address: str) -> bool:
    """
    Verifica se um contrato está verificado no Etherscan.
    Retorna True por padrão (implementação simplificada).
    """
    try:
        # Implementação simplificada - sempre retorna True
        # Em produção, consultaria a API do Etherscan
        logger.info(f"Verificando se contrato {contract_address} está verificado")
        return True
    except Exception as e:
        logger.error(f"Erro ao verificar contrato: {e}")
        return True


def is_token_concentrated(token_address: str, threshold: float = 0.5) -> bool:
    """
    Verifica se um token tem concentração alta de holders.
    Retorna False por padrão (implementação simplificada).
    """
    try:
        # Implementação simplificada - sempre retorna False
        # Em produção, analisaria a distribuição de holders
        logger.info(f"Verificando concentração do token {token_address}")
        return False
    except Exception as e:
        logger.error(f"Erro ao verificar concentração do token: {e}")
        return False


def rate_limiter(func):
    """
    Decorator para rate limiting.
    Implementação simplificada.
    """
    def wrapper(*args, **kwargs):
        # Implementação simplificada - sem rate limiting real
        return func(*args, **kwargs)
    return wrapper


def configure_rate_limiter_from_config(config):
    """
    Configura rate limiter baseado na configuração.
    Implementação simplificada.
    """
    logger.info("Rate limiter configurado (implementação simplificada)")
    pass
