# utils.py

import os
import logging
import requests
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()
log = logging.getLogger(__name__)

# ABI mínimo contendo apenas as funções balanceOf e decimals
MINIMAL_ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


# ---- Conversores seguros ----
def to_int(valor, default: int = 0) -> int:
    """
    Converte valor para int, retornando default em caso de falha.
    """
    try:
        return int(valor)
    except (TypeError, ValueError):
        return default


def to_float(valor, default: float = 0.0) -> float:
    """
    Converte valor para float, retornando default em caso de falha.
    """
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


# =================================
# Configurações Etherscan / BaseScan
# =================================
ETHERSCAN_V1_URL = "https://api.basescan.org/api"
ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID_BASE   = "8453"

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "").strip()
if not ETHERSCAN_API_KEY or len(ETHERSCAN_API_KEY) < 10:
    log.warning("⚠️ ETHERSCAN_API_KEY não configurada ou inválida.")
else:
    log.info(f"[utils] ETHERSCAN_API_KEY carregada: {ETHERSCAN_API_KEY[:6]}...") 


# =========================
# Limitador de Taxa de API
# =========================
class ApiRateLimiter:
    """
    Controla QPS e chamadas diárias para APIs externas, com notificações
    e pausas automáticas quando limites são atingidos.
    """

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
        self.qps_limit       = qps_limit
        self.daily_limit     = daily_limit
        self.warn_pct        = warn_pct
        self.pause_daily_pct = pause_daily_pct
        self.qps_cd          = qps_cooldown_sec
        self.daily_cd        = daily_cooldown_sec
        self.pause_enabled   = pause_enabled

        self.calls_window   = deque()      # timestamps das chamadas no último segundo
        self.daily_count    = 0            # contador do dia corrente
        self.day_anchor     = self._hoje_utc()
        self.paused_until   = None         # datetime até o qual está pausado
        self._notifier      = None
        self._warned_qps    = False
        self._warned_daily  = False

    def _hoje_utc(self) -> datetime:
        agora = datetime.now(timezone.utc)
        return datetime(agora.year, agora.month, agora.day, tzinfo=timezone.utc)

    def _reset_if_new_day(self) -> None:
        agora = datetime.now(timezone.utc)
        if agora >= self.day_anchor + timedelta(days=1):
            self.day_anchor    = self._hoje_utc()
            self.daily_count   = 0
            self._warned_daily = False
            self._notify("🔁 Limite diário de API resetado (novo dia).")

    def set_notifier(self, notifier_callable) -> None:
        """
        Define função de callback para notificações de rate-limit.
        """
        self._notifier = notifier_callable

    def _notify(self, mensagem: str) -> None:
        try:
            if self._notifier:
                self._notifier(mensagem)
            else:
                log.info(f"[API NOTICE] {mensagem}")
        except Exception:
            log.warning("Falha ao enviar notificação do rate limiter.", exc_info=True)

    def is_paused(self) -> bool:
        """
        Retorna True se o limitador estiver em pausa devido a QPS ou daily limit.
        """
        self._reset_if_new_day()
        if self.paused_until is None:
            return False

        agora = datetime.now(timezone.utc)
        if agora >= self.paused_until:
            self.paused_until = None
            self._notify("▶️ Sniper retomado: pausa de limite de API encerrada.")
            return False

        return True

    def before_api_call(self) -> None:
        """
        Deve ser chamado antes de cada requisição a API externa.
        Controla QPS e daily count, lançando RuntimeError se limite for alcançado.
        """
        self._reset_if_new_day()
        if self.is_paused():
            raise RuntimeError("Rate limiter ativo: API pausada")

        agora      = datetime.now(timezone.utc)
        janela_lim = agora - timedelta(seconds=1)

        # Remove timestamps com mais de 1 segundo
        while self.calls_window and self.calls_window[0] < janela_lim:
            self.calls_window.popleft()

        # Aviso próximo ao limite de QPS
        if not self._warned_qps and len(self.calls_window) >= int(self.qps_limit * self.warn_pct):
            self._warned_qps = True
            self._notify(f"⚠️ Aproximando do limite de QPS ({len(self.calls_window)}/{self.qps_limit}/s).")

        # Excedeu QPS
        if len(self.calls_window) >= self.qps_limit:
            if self.pause_enabled:
                self.paused_until = agora + timedelta(seconds=self.qps_cd)
                self._notify(f"⏸️ Pausa automática {self.qps_cd}s: QPS atingido ({self.qps_limit}/s).")
            raise RuntimeError("Rate limiter ativo: QPS excedido")

        # Registra a chamada
        self.calls_window.append(agora)
        self.daily_count += 1

        # Aviso próximo ao limite diário
        if not self._warned_daily and self.daily_count >= int(self.daily_limit * self.warn_pct):
            self._warned_daily = True
            self._notify(f"⚠️ Aproximando do limite diário ({self.daily_count}/{self.daily_limit}).")

        # Excedeu daily threshold
        if self.daily_count >= int(self.daily_limit * self.pause_daily_pct):
            if self.pause_enabled:
                until = min(
                    agora + timedelta(seconds=self.daily_cd),
                    self.day_anchor + timedelta(days=1)
                )
                restante = int((until - agora).total_seconds())
                self.paused_until = until
                self._notify(
                    f"⏸️ Pausa automática diária ({self.daily_count}/{self.daily_limit}). "
                    f"Retoma em ~{restante}s ou no reset diário."
                )
            raise RuntimeError("Rate limiter ativo: daily threshold atingido")


# instância global do limitador de API
rate_limiter = ApiRateLimiter(
    qps_limit=5,
    daily_limit=100_000,
    warn_pct=0.85,
    pause_daily_pct=0.95,
    qps_cooldown_sec=10,
    daily_cooldown_sec=3600,
    pause_enabled=True
)


def configure_rate_limiter_from_config(cfg: dict) -> None:
    """
    Ajusta parâmetros do rate_limiter a partir do dicionário de configuração.
    """
    rate_limiter.qps_limit       = to_int(cfg.get("RATE_QPS_LIMIT"),     rate_limiter.qps_limit)
    rate_limiter.daily_limit     = to_int(cfg.get("RATE_DAILY_LIMIT"),   rate_limiter.daily_limit)
    rate_limiter.warn_pct        = to_float(cfg.get("RATE_WARN_PCT"),    rate_limiter.warn_pct)
    rate_limiter.pause_daily_pct = to_float(cfg.get("RATE_PAUSE_DAILY_PCT"), rate_limiter.pause_daily_pct)
    rate_limiter.qps_cd          = to_int(cfg.get("RATE_QPS_COOLDOWN_SEC"), rate_limiter.qps_cd)
    rate_limiter.daily_cd        = to_int(cfg.get("RATE_DAILY_COOLDOWN_SEC"), rate_limiter.daily_cd)
    rate_limiter.pause_enabled   = str(cfg.get("PAUSE_SNIPER_ON_RATE_LIMIT", rate_limiter.pause_enabled)).lower() in {"1", "true", "yes"}


# ==================
# Helpers Etherscan
# ==================
def is_contract_verified(token_address: str, api_key: str = ETHERSCAN_API_KEY) -> bool:
    """
    Verifica no Etherscan/BaseScan se o contrato está verificado.
    Retorna False se não verificado, True caso verificado ou se não houver API key.
    """
    rate_limiter.before_api_call()
    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY ausente — pulando verificação de contrato.")
        return True

    params = {
        "module": "contract",
        "action": "getsourcecode",
        "address": token_address,
        "chainid": CHAIN_ID_BASE,
        "apikey": api_key,
    }

    try:
        resp = requests.get(ETHERSCAN_V2_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # status "1" e resultado não vazio indicam contrato verificado
        if data.get("status") != "1" or not data.get("result"):
            log.warning(f"[Verificação] Contrato {token_address} NÃO verificado.")
            return False
        src = data["result"][0].get("SourceCode", "")
        return bool(src)
    except Exception as e:
        log.error(f"Erro ao verificar contrato {token_address}: {e}", exc_info=True)
        return False


def is_token_concentrated(
    token_address: str,
    api_key: str = ETHERSCAN_API_KEY,
    top_limit_pct: float = 50.0
) -> bool:
    """
    Verifica se existe holder com percentual >= top_limit_pct.
    Retorna True se encontrar concentração ou em caso de erro; False caso contrário.
    """
    rate_limiter.before_api_call()
    if not api_key:
        log.warning("⚠️ ETHERSCAN_API_KEY ausente — pulando verificação de concentração.")
        return False

    params = {
        "module": "token",
        "action": "tokenholderlist",
        "contractaddress": token_address,
        "chainid": CHAIN_ID_BASE,
        "apikey": api_key,
    }

    try:
        resp = requests.get(ETHERSCAN_V2_URL, params=params, timeout=15)
        resp.raise_for_status()
        holders = resp.json().get("result", [])
        if not isinstance(holders, list):
            log.error(f"Resposta inesperada de tokenholderlist: {holders}")
            return True

        for holder in holders:
            pct_str = str(holder.get("Percentage", "0")).replace("%", "").strip()
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0.0
            if pct >= top_limit_pct:
                return True

        return False
    except Exception as e:
        log.error(f"Erro ao verificar concentração de holders {token_address}: {e}", exc_info=True)
        return True


def testar_etherscan_v2(
    api_key: str = ETHERSCAN_API_KEY,
    address: str = "0x4200000000000000000000000000000000000006"
) -> bool:
    """
    Testa a conectividade e validade da API Key no Etherscan/BaseScan.
    Retorna True se obtiver sucesso em até 3 tentativas.
    """
    import time

    if not api_key:
        log.error("❌ ETHERSCAN_API_KEY não encontrada para teste.")
        return False

    params = {
        "chainid": CHAIN_ID_BASE,
        "module": "contract",
        "action": "getsourcecode",
        "address": address,
        "apikey": api_key,
    }

    for _ in range(3):
        inicio = time.time()
        try:
            resp = requests.get(ETHERSCAN_V2_URL, params=params, timeout=30)
            data = resp.json()
            if data.get("status") == "1":
                return True
        except Exception:
            continue
    return False


def get_token_balance(
    web3: Web3,
    token_address: str,
    wallet_address: str,
    abi: list = MINIMAL_ERC20_ABI
) -> Decimal:
    """
    Consulta saldo de token ERC-20 na carteira e retorna Decimal.
    Em caso de erro, retorna Decimal(0).
    """
    try:
        token_addr  = Web3.to_checksum_address(token_address)
        wallet_addr = Web3.to_checksum_address(wallet_address)
        contrato    = web3.eth.contract(address=token_addr, abi=abi)

        raw_balance = contrato.functions.balanceOf(wallet_addr).call()
        decimals    = contrato.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10 ** decimals)
    except Exception as e:
        log.error(
            f"[get_token_balance] Erro consultando saldo do token {token_address} "
            f"na wallet {wallet_address}: {e}",
            exc_info=True
        )
        return Decimal(0)


def has_high_tax(token_address: str, max_tax_pct: float) -> bool:
    """
    Verifica se o token aplica taxação acima de max_tax_pct.
    Esta função ainda é stub (retorna sempre False).
    Para implementar:
      1) Simular swap mínimo com DexClient.get_amounts_out
      2) Calcular diferença input/output
      3) Retornar True se taxa > max_tax_pct
    """
    return False
