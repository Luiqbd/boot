import os
import requests
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# ===========================
# Configuração de Endpoints
# ===========================
ETHERSCAN_V1_URL = "https://api.basescan.org/api"       # API legado BaseScan
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"    # API multichain Etherscan
CHAIN_ID_BASE = "8453"  # Base Mainnet (Etherscan V2)

# Leitura da chave de API
BASESCAN_API_KEY = (
    os.getenv("ETHERSCAN_API_KEY")
    or os.getenv("BASESCAN_API_KEY")
)

def is_v2_key(api_key) -> bool:
    """Retorna True se a chave parece ser do formato Etherscan API V2 multichain."""
    if not isinstance(api_key, str) or not api_key:
        return False
    return api_key.startswith("CX") or len(api_key) > 40

# ===========================
# Rate Limiter
# ===========================
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

# Instância global
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
    """Permite configurar o rate limiter via dicionário de config."""
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

def is_contract_verified(token_address: str, api_key: str = BASESCAN_API_KEY) -> bool:
    """
    Verifica se um contrato está verificado na Base (ChainID 8453) usando Etherscan API V2 multichain.
    Mantém compatibilidade com API V1 legado.
    Retorna True se verificado, False caso contrário.
    """
    rate_limiter.before_api_call()

    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY não configurada — pulando verificação de contrato.")
        return True

    if is_v2_key(api_key):
        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": token_address,
            "chainid": CHAIN_ID_BASE,
            "apikey": api_key
        }
        url = ETHERSCAN_V2_URL
    else:
        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": token_address,
            "apikey": api_key
        }
        url = ETHERSCAN_V1_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "1" or not isinstance(data.get("result"), list) or not data["result"]:
            log.warning(f"[Verificação] Contrato {token_address} NÃO verificado. Resposta: {data}")
            return False

        source_code = data["result"][0].get("SourceCode", "")
        if not source_code:
            log.warning(f"[Verificação] Contrato {token_address} encontrado, mas sem código-fonte.")
            return False

        contract_name = data["result"][0].get("ContractName", "N/A")
        log.info(f"[Verificação] Contrato verificado: {contract_name} ({token_address})")
        return True

    except Exception as e:
        log.error(f"Erro ao verificar contrato {token_address}: {e}", exc_info=True)
        return False

def is_token_concentrated(token_address: str, top_limit_pct: float, api_key: str = BASESCAN_API_KEY) -> bool:
    """
    Verifica se um token está concentrado em poucos holders acima do limite (top_limit_pct).
    Suporta Etherscan API V2 multichain e mantém fallback para API V1 legado.
    Retorna True se o token for concentrado.
    """
    rate_limiter.before_api_call()

    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY não configurada — pulando verificação de concentração.")
        return False  # ou True, se preferir considerar concentrado por segurança

    if is_v2_key(api_key):
        # API V2 — multichain
        params = {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": token_address,
            "chainid": CHAIN_ID_BASE,
            "apikey": api_key
        }
        url = ETHERSCAN_V2_URL
    else:
        # API V1 — BaseScan
        params = {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": token_address,
            "apikey": api_key
        }
        url = ETHERSCAN_V1_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", [])
        if not isinstance(result, list):
            log.error(f"Resposta inesperada do explorer: {result}")
            return True  # Conservador: assume concentrado

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
        return True  # Conservador: assume concentrado


def testar_etherscan_v2(api_key: str = BASESCAN_API_KEY, address: str = "0x4200000000000000000000000000000000000006"):
    """
    Testa a conexão com o Etherscan API V2 na Base (chainid=8453).
    Faz até 3 tentativas, aumenta o timeout e loga o tempo de resposta.
    """
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

    log.info(f"➡️ Iniciando teste de conexão Etherscan V2 (Base). Chave: {api_key}")
    for tentativa in range(1, 4):
        inicio = time.time()
        try:
            resp = requests.get(url, params=params, timeout=30)
            duracao = time.time() - inicio
            log.info(f"[Tentativa {tentativa}] Tempo: {duracao:.2f}s | Status HTTP: {resp.status_code}")
            data = resp.json()
            log.info(f"[Tentativa {tentativa}] Resposta: {data}")

            if data.get("status") == "1":
                log.info("✅ Teste bem-sucedido — Etherscan V2 está respondendo corretamente.")
                return True
            else:
                log.warning(f"⚠️ Resposta sem sucesso na tentativa {tentativa}: {data}")

        except requests.exceptions.ReadTimeout:
            log.warning(f"⏳ Timeout na tentativa {tentativa} após {time.time() - inicio:.2f}s")
        except Exception as e:
            log.error(f"❌ Erro na tentativa {tentativa}: {e}", exc_info=True)

    log.error("❌ Todas as tentativas falharam.")
    return False
