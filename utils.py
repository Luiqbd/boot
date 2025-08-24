import os
import requests
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Endpoints (V1 legacy e V2 multichain)
ETHERSCAN_V1_URL = "https://api.basescan.org/api"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID_BASE = "8453"  # Base Mainnet (Etherscan V2 chainid)

# Chave do ambiente (pode ser única do Etherscan)
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY") or os.getenv("ETHERSCAN_API_KEY")


def is_v2_key(api_key) -> bool:
    """Detecta se a chave parece ser do formato v2 (Etherscan Multichain)."""
    if not isinstance(api_key, str) or not api_key:
        return False
    return api_key.startswith("CX") or len(api_key) > 40


class ApiRateLimiter:
    def __init__(
        self,
        qps_limit: int,
        daily_limit: int,
        warn_pct: float,
        pause_daily_pct: float,
        qps_cooldown_sec: int,
        daily_cooldown_sec: int,
        pause_enabled: bool = True
    ):
        self.qps_limit = qps_limit
        self.daily_limit = daily_limit
        self.warn_pct = warn_pct
        self.pause_daily_pct = pause_daily_pct
        self.qps_cd = qps_cooldown_sec
        self.daily_cd = daily_cooldown_sec
        self.pause_enabled = pause_enabled

        self.calls_window = deque()
        self.daily_count = 0
        self.day_anchor = self._today_utc()
        self.paused_until: datetime | None = None

        self._notifier = None
        self._warned_daily = False
        self._warned_qps = False

    def _today_utc(self):
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _reset_daily_if_needed(self):
        now = datetime.now(timezone.utc)
        if now >= self.day_anchor + timedelta(days=1):
            self.day_anchor = self._today_utc()
            self.daily_count = 0
            self._warned_daily = False
            self._notify("🔁 Limite diário de API resetado (novo dia).")

    def set_notifier(self, notify_callable):
        self._notifier = notify_callable

    def _notify(self, msg: str):
        try:
            if self._notifier:
                self._notifier(msg)
            else:
                log.info(f"[API NOTICE] {msg}")
        except Exception:
            log.warning("Falha ao notificar rate limiter.", exc_info=True)

    def is_paused(self) -> bool:
        self._reset_daily_if_needed()
        if self.paused_until is None:
            return False
        now = datetime.now(timezone.utc)
        if now >= self.paused_until:
            self.paused_until = None
            self._notify("▶️ Sniper retomado: pausa de limite de API encerrada.")
            return False
        return True

    def before_api_call(self):
        self._reset_daily_if_needed()

        if self.is_paused():
            raise RuntimeError("API rate-limited: paused")

        now = datetime.now(timezone.utc)
        one_sec_ago = now - timedelta(seconds=1)
        while self.calls_window and self.calls_window[0] < one_sec_ago:
            self.calls_window.popleft()

        if not self._warned_qps and len(self.calls_window) >= int(self.qps_limit * self.warn_pct):
            self._warned_qps = True
            self._notify(f"⚠️ Aproximando do limite de QPS ({len(self.calls_window)}/{self.qps_limit}/s).")

        if len(self.calls_window) >= self.qps_limit:
            if self.pause_enabled:
                self.paused_until = now + timedelta(seconds=self.qps_cd)
                self._notify(f"⏸️ Pausa automática {self.qps_cd}s: limite de QPS atingido ({self.qps_limit}/s).")
            raise RuntimeError("API rate-limited: QPS exceeded")

        self.calls_window.append(now)
        self.daily_count += 1

        if not self._warned_daily and self.daily_count >= int(self.daily_limit * self.warn_pct):
            self._warned_daily = True
            self._notify(f"⚠️ Aproximando do limite diário ({self.daily_count}/{self.daily_limit}).")

        if self.daily_count >= int(self.daily_limit * self.pause_daily_pct):
            if self.pause_enabled:
                until = min(
                    now + timedelta(seconds=self.daily_cd),
                    self.day_anchor + timedelta(days=1)
                )
                self.paused_until = until
                restante = int((until - now).total_seconds())
                self._notify(
                    f"⏸️ Pausa automática: consumo diário alto "
                    f"({self.daily_count}/{self.daily_limit}). Retoma em ~{restante}s ou no reset diário."
                )
            raise RuntimeError("API rate-limited: daily threshold reached")


rate_limiter = ApiRateLimiter(
    qps_limit=5,
    daily_limit=100000,
    warn_pct=0.85,
    pause_daily_pct=0.95,
    qps_cooldown_sec=10,
    daily_cooldown_sec=3600,
    pause_enabled=True
)


def configure_rate_limiter_from_config(config):
    try:
        rate_limiter.qps_limit = int(config.get("RATE_QPS_LIMIT", rate_limiter.qps_limit))
        rate_limiter.daily_limit = int(config.get("RATE_DAILY_LIMIT", rate_limiter.daily_limit))
        rate_limiter.warn_pct = float(config.get("RATE_WARN_PCT", rate_limiter.warn_pct))
        rate_limiter.pause_daily_pct = float(config.get("RATE_PAUSE_DAILY_PCT", rate_limiter.pause_daily_pct))
        rate_limiter.qps_cd = int(config.get("RATE_QPS_COOLDOWN_SEC", rate_limiter.qps_cd))
        rate_limiter.daily_cd = int(config.get("RATE_DAILY_COOLDOWN_SEC", rate_limiter.daily_cd))
        rate_limiter.pause_enabled = bool(config.get("PAUSE_SNIPER_ON_RATE_LIMIT", rate_limiter.pause_enabled))
    except Exception:
        log.warning("Falha ao aplicar configs do rate limiter.", exc_info=True)

def is_token_concentrated(token_address: str, top_limit_pct: float, api_key: str = BASESCAN_API_KEY) -> bool:
    """
    Verifica se um token está concentrado em poucos holders acima de top_limit_pct.
    Agora suporta Etherscan API V2 multichain (usando chainid para rede Base).
    """
    rate_limiter.before_api_call()

    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY não configurada — pulando verificação de concentração.")
        return False  # ou True se quiser ser mais restritivo

    if is_v2_key(api_key):
        # V2 multichain — chainid 8453 = Base Mainnet
        params = {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": token_address,
            "chainid": "8453",
            "apikey": api_key
        }
        url = ETHERSCAN_V2_URL
    else:
        # V1 legacy (BaseScan antigo)
        params = {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": token_address,
            "apikey": api_key
        }
        url = ETHERSCAN_V1_URL

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", [])
        if not isinstance(result, list):
            log.error(f"Resposta inesperada do explorer: {result}")
            return True  # ser conservador: assume concentrado

        for holder in result:
            pct_str = str(holder.get("Percentage", "0")).replace("%", "").strip()
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0.0
            if pct >= top_limit_pct:
                return True
        return False

    except Exception as e:
        log.error(f"Erro ao verificar concentração de holders: {e}", exc_info=True)
        return True  # por segurança, assume concentrado
