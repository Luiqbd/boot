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

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ABIFunctionNotFound, ContractLogicError

from config import config

logger = logging.getLogger(__name__)

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
    Escapa caracteres especiais para MarkdownV2.
    """
    return re.sub(r'([._\\-\\*\\[\\]\\(\\)~`>#+=|{}.!])', r'\\\1', text)


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


def configure_rate_limiter_from_config(cfg: Dict[str, Any]) -> None:
    """
    Ajusta parâmetros do rate_limiter a partir do dict de config.
    """
    try:
        rate_limiter.qps_limit       = int(cfg.get("RATE_QPS_LIMIT", rate_limiter.qps_limit))
        rate_limiter.daily_limit     = int(cfg.get("RATE_DAILY_LIMIT", rate_limiter.daily_limit))
        rate_limiter.warn_pct        = float(cfg.get("RATE_WARN_PCT", rate_limiter.warn_pct))
        rate_limiter.pause_daily_pct = float(cfg.get("RATE_PAUSE_DAILY_PCT", rate_limiter.pause_daily_pct))
        rate_limiter.qps_cd          = int(cfg.get("RATE_QPS_COOLDOWN_SEC", rate_limiter.qps_cd))
        rate_limiter.daily_cd        = int(cfg.get("RATE_DAILY_COOLDOWN_SEC", rate_limiter.daily_cd))
        rate_limiter.pause_enabled   = bool(cfg.get("PAUSE_SNIPER_ON_RATE_LIMIT", rate_limiter.pause_enabled))
    except Exception:
        logger.warning("Falha ao configurar rate limiter.", exc_info=True)


# -------------------------------------------------------------------
# Verificações on-chain via Etherscan/BaseScan
# -------------------------------------------------------------------

ETHERSCAN_KEY = config.get("ETHERSCAN_API_KEY", "")
ETHERSCAN_V1 = "https://api.basescan.org/api"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"
CHAIN_ID_BASE = "8453"


def is_contract_verified(token: str, api_key: str = ETHERSCAN_KEY) -> bool:
    """
    Consulta Etherscan/BaseScan para checar contrato verificado.
    """
    rate_limiter.before_api_call()
    if not api_key:
        logger.warning("ETHERSCAN_API_KEY ausente, pulando verificação.")
        return True

    params = {
        "module":   "contract",
        "action":   "getsourcecode",
        "address":  token,
        "chainid":  CHAIN_ID_BASE,
        "apikey":   api_key
    }
    url = ETHERSCAN_V2 if api_key else ETHERSCAN_V1

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1" or not data.get("result"):
            logger.warning(f"Contrato {token} NÃO verificado.")
            return False
        code = data["result"][0].get("SourceCode", "")
        if not code:
            logger.warning(f"Contrato {token} sem código-fonte.")
            return False
        logger.info(f"Contrato verificado: {token}.")
        return True
    except Exception as e:
        logger.error(f"Erro verificação contrato {token}: {e}", exc_info=True)
        return False


def is_token_concentrated(
    token: str,
    top_limit_pct: float,
    api_key: str = ETHERSCAN_KEY
) -> bool:
    """
    Consulta lista de holders e retorna True se algum >= top_limit_pct%.
    """
    rate_limiter.before_api_call()
    if not api_key:
        logger.warning("ETHERSCAN_API_KEY ausente, pulando concentração.")
        return False

    params = {
        "module":          "token",
        "action":          "tokenholderlist",
        "contractaddress": token,
        "chainid":         CHAIN_ID_BASE,
        "apikey":          api_key
    }
    url = ETHERSCAN_V2 if api_key else ETHERSCAN_V1

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        holders = resp.json().get("result", [])
        if not isinstance(holders, list):
            logger.error(f"Resposta holders inesperada: {holders}")
            return True
        for h in holders:
            pct = float(str(h.get("Percentage", "0")).strip().replace("%", "")) or 0.0
            if pct >= top_limit_pct:
                return True
        return False
    except Exception as e:
        logger.error(f"Erro concentração holders: {e}", exc_info=True)
        return True


def testar_etherscan_v2(
    api_key: str = ETHERSCAN_KEY,
    address: str = "0x4200000000000000000000000000000000000006"
) -> bool:
    """
    Testa conexão Etherscan V2 (até 3 tentativas) e loga tempos.
    """
    if not api_key:
        logger.error("Nenhuma ETHERSCAN_API_KEY para teste.")
        return False

    params = {
        "module":  "contract",
        "action":  "getsourcecode",
        "address": address,
        "chainid": CHAIN_ID_BASE,
        "apikey":  api_key
    }
    url = ETHERSCAN_V2

    for i in range(1, 4):
        start = time.time()
        try:
            resp = requests.get(url, params=params, timeout=30)
            dur = time.time() - start
            logger.info(f"[Teste {i}] {dur:.2f}s HTTP {resp.status_code}")
            data = resp.json()
            if data.get("status") == "1":
                logger.info("Etherscan V2 OK.")
                return True
        except Exception as e:
            logger.warning(f"[Teste {i}] falha: {e}", exc_info=True)
    logger.error("Teste Etherscan V2 falhou.")
    return False


# -------------------------------------------------------------------
# Outros utilitários
# -------------------------------------------------------------------

def has_high_tax(
    client: Any,
    token_address: str,
    token_in_weth: str,
    sample_amount_wei: int = Web3.to_wei(Decimal("0.01"), "ether"),
    max_tax_bps: int = 500
) -> bool:
    """
    Stub: detecta taxa on-transfer maior que max_tax_bps.
    Atualmente sempre retorna False.
    """
    logger.debug(f"has_high_tax stub: token={token_address}, sample={sample_amount_wei}")
    return False


def get_token_balance(
    client: Any,
    token_address: str
) -> int:
    """
    Retorna saldo bruto (units) de token_address para a carteira de client.
    Se falhar, retorna 0.
    """
    try:
        addr = Web3.to_checksum_address(token_address)
        contract = client.web3.eth.contract(address=addr, abi=client.erc20_abi)
        bal = contract.functions.balanceOf(client.wallet).call()
        logger.debug(f"Balance raw {addr}: {bal}")
        return int(bal)
    except (BadFunctionCallOutput, ABIFunctionNotFound, ContractLogicError) as e:
        logger.error(f"Erro on-chain balanceOf {token_address}: {e}", exc_info=True)
        return 0
    except Exception as e:
        logger.error(f"Erro inesperado balanceOf {token_address}: {e}", exc_info=True)
        return 0
