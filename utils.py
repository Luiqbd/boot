# utils.py
import os
import requests
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ---- Safe converters ----
def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

def to_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

# ===========================
# Etherscan / BaseScan configs
# ===========================
ETHERSCAN_V1_URL = "https://api.basescan.org/api"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID_BASE  = "8453"

# load key
ETHERSCAN_API_KEY = str(os.getenv("ETHERSCAN_API_KEY", "")).strip()
if not ETHERSCAN_API_KEY or len(ETHERSCAN_API_KEY) < 10:
    log.warning("⚠️ ETHERSCAN_API_KEY não configurada ou inválida.")
else:
    log.info(f"[INFO] ETHERSCAN_API_KEY carregada: {ETHERSCAN_API_KEY[:6]}...")


# ================
# Rate Limiter
# ================
class ApiRateLimiter:
    def __init__(self, qps_limit, daily_limit, warn_pct,
                 pause_daily_pct, qps_cooldown_sec,
                 daily_cooldown_sec, pause_enabled=True):
        self.qps_limit       = qps_limit
        self.daily_limit     = daily_limit
        self.warn_pct        = warn_pct
        self.pause_daily_pct = pause_daily_pct
        self.qps_cd          = qps_cooldown_sec
        self.daily_cd        = daily_cooldown_sec
        self.pause_enabled   = pause_enabled

        self.calls_window     = deque()
        self.daily_count      = 0
        self.day_anchor       = self._today_utc()
        self.paused_until     = None
        self._notifier       = None
        self._warned_daily   = False
        self._warned_qps     = False

    def _today_utc(self):
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _reset_daily_if_needed(self):
        now = datetime.now(timezone.utc)
        if now >= self.day_anchor + timedelta(days=1):
            self.day_anchor     = self._today_utc()
            self.daily_count    = 0
            self._warned_daily  = False
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
                    f"⏸️ Pausa automática dia alto ({self.daily_count}/{self.daily_limit}). "
                    f"Retoma em ~{restante}s ou no reset diário."
                )
            raise RuntimeError("API rate-limited: daily threshold reached")


rate_limiter = ApiRateLimiter(
    qps_limit=5,
    daily_limit=100_000,
    warn_pct=0.85,
    pause_daily_pct=0.95,
    qps_cooldown_sec=10,
    daily_cooldown_sec=3600,
    pause_enabled=True
)


def configure_rate_limiter_from_config(cfg: dict):
    rate_limiter.qps_limit       = to_int(cfg.get("RATE_QPS_LIMIT"),    rate_limiter.qps_limit)
    rate_limiter.daily_limit     = to_int(cfg.get("RATE_DAILY_LIMIT"),  rate_limiter.daily_limit)
    rate_limiter.warn_pct        = to_float(cfg.get("RATE_WARN_PCT"),    rate_limiter.warn_pct)
    rate_limiter.pause_daily_pct = to_float(cfg.get("RATE_PAUSE_DAILY_PCT"), rate_limiter.pause_daily_pct)
    rate_limiter.qps_cd          = to_int(cfg.get("RATE_QPS_COOLDOWN_SEC"), rate_limiter.qps_cd)
    rate_limiter.daily_cd        = to_int(cfg.get("RATE_DAILY_COOLDOWN_SEC"), rate_limiter.daily_cd)
    rate_limiter.pause_enabled   = str(cfg.get("PAUSE_SNIPER_ON_RATE_LIMIT", rate_limiter.pause_enabled)).lower() in {"1","true","yes"}


# ================
# Etherscan Helpers
# ================
def is_contract_verified(token_address: str, api_key: str = ETHERSCAN_API_KEY) -> bool:
    rate_limiter.before_api_call()
    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY não configurada — pulando verificação de contrato.")
        return True

    params = {
        "module": "contract",
        "action": "getsourcecode",
        "address": token_address,
        "chainid": CHAIN_ID_BASE,
        "apikey": api_key
    }
    url = ETHERSCAN_V2_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1" or not data.get("result"):
            log.warning(f"[Verificação] Contrato {token_address} NÃO verificado. URL: {url} {params}")
            return False
        src = data["result"][0].get("SourceCode", "")
        return bool(src)
    except Exception as e:
        log.error(f"Erro ao verificar contrato {token_address}: {e}", exc_info=True)
        return False


def is_token_concentrated(token_address: str, api_key: str = ETHERSCAN_API_KEY, top_limit_pct: float = 50.0) -> bool:
    rate_limiter.before_api_call()
    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY não configurada — pulando verificação de concentração.")
        return False

    params = {
        "module": "token",
        "action": "tokenholderlist",
        "contractaddress": token_address,
        "chainid": CHAIN_ID_BASE,
        "apikey": api_key
    }
    url = ETHERSCAN_V2_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("result", [])
        if not isinstance(data, list):
            log.error(f"Resposta inesperada do explorer: {data}")
            return True
        for holder in data:
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
        return True


def testar_etherscan_v2(api_key: str = ETHERSCAN_API_KEY,
                        address: str = "0x4200000000000000000000000000000000000006") -> bool:
    import time
    if not api_key:
        log.error("❌ Nenhuma API Key encontrada para teste.")
        return False

    url = ETHERSCAN_V2_URL
    params = {
        "chainid": CHAIN_ID_BASE,
        "module": "contract",
        "action": "getsourcecode",
        "address": address,
        "apikey": api_key
    }

    for tentativa in range(1, 4):
        inicio = time.time()
        try:
            resp = requests.get(url, params=params, timeout=30)
            dur = time.time() - inicio
            data = resp.json()
            if data.get("status") == "1":
                return True
        except Exception:
            pass
    return False
