import os
import time
import requests
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from web3 import Web3
from dotenv import load_dotenv

from exchange_client import ExchangeClient

# Carrega variáveis de ambiente de um arquivo .env, se existir
load_dotenv()

log = logging.getLogger(__name__)

# ===========================
# Configuração de Endpoints
# ===========================
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
ETHERSCAN_V1_URL = "https://api.basescan.org/api"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID_BASE = "8453"  # Base Mainnet (Etherscan V2)

# Verifica se a chave foi carregada
if not ETHERSCAN_API_KEY or len(ETHERSCAN_API_KEY) < 10:
    log.warning("⚠️ ETHERSCAN_API_KEY não configurada ou inválida.")
else:
    log.info(f"[INFO] ETHERSCAN_API_KEY carregada: {ETHERSCAN_API_KEY[:6]}...")


def is_v2_key(api_key: str) -> bool:
    """
    Retorna True se a chave fornecida for válida para o Etherscan V2.
    """
    return bool(api_key)


# ===========================
# Rate Limiter
# ===========================
class ApiRateLimiter:
    """
    Limita as chamadas à API para QPS e total diário,
    com notificações via callback.
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
        self.paused_until = None

        self._notifier = None
        self._warned_qps = False
        self._warned_daily = False

    def _today_utc(self) -> datetime:
        now = datetime.now(timezone.utc)
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def _reset_daily_if_needed(self) -> None:
        now = datetime.now(timezone.utc)
        if now >= self.day_anchor + timedelta(days=1):
            self.day_anchor = self._today_utc()
            self.daily_count = 0
            self._warned_daily = False
            self._notify("🔁 Limite diário de API resetado (novo dia).")

    def set_notifier(self, notifier_callable) -> None:
        """
        Define uma função callback(msg: str) para receber avisos.
        """
        self._notifier = notifier_callable

    def _notify(self, msg: str) -> None:
        try:
            if self._notifier:
                self._notifier(msg)
            else:
                log.info(f"[RATE LIMITER] {msg}")
        except Exception:
            log.warning("Falha ao notificar rate limiter.", exc_info=True)

    def is_paused(self) -> bool:
        """
        Retorna True se estiver em pausa por limite de QPS ou diário.
        """
        self._reset_daily_if_needed()
        if not self.paused_until:
            return False
        now = datetime.now(timezone.utc)
        if now >= self.paused_until:
            self.paused_until = None
            self._notify("▶️ Sniper retomado: pausa de limite de API encerrada.")
            return False
        return True

    def before_api_call(self) -> None:
        """
        Deve ser chamado antes de cada request ao explorer.
        Lança RuntimeError se os limites de QPS ou diário estiverem excedidos.
        """
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
                self._notify(f"⏸️ Pausa automática {self.qps_cd}s: QPS atingido ({self.qps_limit}/s).")
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
                    f"({self.daily_count}/{self.daily_limit}). Retoma em ~{restante}s."
                )
            raise RuntimeError("API rate-limited: daily threshold reached")


# Instância global do rate limiter
rate_limiter = ApiRateLimiter()


def configure_rate_limiter_from_config(config: dict) -> None:
    """
    Ajusta parâmetros do rate_limiter com base em um dicionário de configuração.
    """
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


# ===========================
# Verificações no Explorer
# ===========================
def is_contract_verified(token_address: str, api_key: str = ETHERSCAN_API_KEY) -> bool:
    """
    Consulta Etherscan/BaseScan para verificar se o contrato está verificado.
    """
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
    url = ETHERSCAN_V2_URL if is_v2_key(api_key) else ETHERSCAN_V1_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "1" or not isinstance(data.get("result"), list) or not data["result"]:
            log.warning(f"[Verificação] Contrato {token_address} NÃO verificado.")
            return False

        source_code = data["result"][0].get("SourceCode", "")
        if not source_code:
            log.warning(f"[Verificação] Contrato {token_address} sem código-fonte.")
            return False

        name = data["result"][0].get("ContractName", "N/A")
        log.info(f"[Verificação] Contrato verificado: {name} ({token_address})")
        return True

    except Exception as e:
        log.error(f"Erro ao verificar contrato {token_address}: {e}", exc_info=True)
        return False


def is_token_concentrated(token_address: str, top_limit_pct: float, api_key: str = ETHERSCAN_API_KEY) -> bool:
    """
    Verifica se há holder com participação >= top_limit_pct do supply total.
    """
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
    url = ETHERSCAN_V2_URL if is_v2_key(api_key) else ETHERSCAN_V1_URL

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        holders = resp.json().get("result", [])

        if not isinstance(holders, list):
            log.error(f"Resposta inesperada ao listar holders: {holders}")
            return True

        for h in holders:
            pct_str = str(h.get("Percentage", "0")).replace("%", "").strip()
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


def testar_etherscan_v2(
    api_key: str = ETHERSCAN_API_KEY,
    address: str = "0x4200000000000000000000000000000000000006"
) -> bool:
    """
    Testa a conexão com a API Etherscan V2 na Base (chainid=8453).
    Faz até 3 tentativas, aumenta timeout e loga o tempo de resposta.
    """
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

    log.info("➡️ Iniciando teste de conexão com Etherscan V2 (Base).")
    for tentativa in range(1, 4):
        inicio = time.time()
        try:
            resp = requests.get(url, params=params, timeout=30)
            duracao = time.time() - inicio
            log.info(f"[Tentativa {tentativa}] Tempo: {duracao:.2f}s | HTTP: {resp.status_code}")
            data = resp.json()
            log.debug(f"[Tentativa {tentativa}] Resposta: {data}")

            if data.get("status") == "1":
                log.info("✅ Teste bem-sucedido — Etherscan V2 está respondendo corretamente.")
                return True
            else:
                log.warning(f"⚠️ Resposta sem sucesso ({tentativa}): {data}")

        except requests.exceptions.ReadTimeout:
            log.warning(f"⏳ Timeout na tentativa {tentativa} após {time.time() - inicio:.2f}s")
        except Exception as e:
            log.error(f"❌ Erro na tentativa {tentativa}: {e}", exc_info=True)

    log.error("❌ Todas as tentativas de teste falharam.")
    return False


def has_high_tax(
    client: ExchangeClient,
    token_address: str,
    token_in_weth: str,
    sample_amount_wei: int = Web3.to_wei(Decimal("0.01"), "ether"),
    max_tax_bps: int = 500
) -> bool:
    """
    Verifica se o token aplica taxa de transferência (tax) maior que max_tax_bps.

    Stub atual; sempre retorna False.
    """
    log.debug(
        "has_high_tax stub: token=%s sample_amount=%d",
        token_address, sample_amount_wei
    )
    # TODO: implementar lógica real de detecção de tax on-transfer
    return False


def get_token_balance(
    client: ExchangeClient,
    token_address: str
) -> int:
    """
    Retorna o saldo bruto (raw, em unidades base) do token na carteira do client.
    """
    token_address = Web3.to_checksum_address(token_address)
    contract = client.web3.eth.contract(
        address=token_address,
        abi=client.erc20_abi
    )
    balance_raw = contract.functions.balanceOf(client.wallet).call()
    log.debug("Saldo raw de %s: %d", token_address, balance_raw)
    return int(balance_raw)
