import os
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

logger = logging.getLogger(__name__)
load_dotenv()

def str_to_bool(val: Union[str, bool]) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "t", "yes", "y"}

def get_env(
    key: str,
    default: Optional[Any] = None,
    cast: Any = str,
    required: bool = False
) -> Any:
    raw = os.getenv(key, None)
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if required and default is None:
            raise RuntimeError(f"Variável obrigatória '{key}' não informada")
        raw = default
    try:
        return cast(raw) if raw is not None else raw
    except Exception as e:
        raise RuntimeError(f"Falha ao converter '{key}'={raw}: {e}")

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY vazia")
    key = pk.lower().removeprefix("0x")
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("PRIVATE_KEY inválida")
    return key

def to_checksum(addr: str, nome: str) -> str:
    if not WEB3_AVAILABLE:
        # Validação básica sem Web3
        if not addr or not addr.startswith('0x') or len(addr) != 42:
            raise ValueError(f"Endereço '{nome}' inválido: {addr}")
        return addr
    
    if not Web3.is_address(addr):
        raise ValueError(f"Endereço '{nome}' inválido: {addr}")
    return Web3.to_checksum_address(addr)

@dataclass(frozen=True)
class DexConfig:
    name: str
    factory: str
    router: str
    type: str  # 'v2' ou 'v3'

def load_dexes() -> List[DexConfig]:
    dexes: List[DexConfig] = []
    idx = 1
    while True:
        prefix = f"DEX_{idx}_"
        nome = os.getenv(prefix + "NAME")
        if not nome:
            break
        factory = to_checksum(get_env(prefix + "FACTORY", required=True), f"{nome} factory")
        router  = to_checksum(get_env(prefix + "ROUTER",  required=True), f"{nome} router")
        dtype   = get_env(prefix + "TYPE", default="v2").lower()
        if dtype not in ("v2", "v3"):
            raise ValueError(f"Tipo inválido para {nome}: {dtype}")
        dexes.append(DexConfig(name=nome, factory=factory, router=router, type=dtype))
        idx += 1
    if not dexes:
        logger.warning("Nenhuma DEX configurada. Verifique DEX_1_* no .env")
    return dexes

# ─── Core settings ────────────────────────────────────────────────────
RPC_URL    = get_env("RPC_URL", default="https://mainnet.base.org")
CHAIN_ID   = get_env("CHAIN_ID", default=8453, cast=int)

PRIVATE_KEY = normalize_private_key(get_env("PRIVATE_KEY", required=True))
WALLET      = get_env("WALLET_ADDRESS", default=None)
if WALLET:
    WALLET = to_checksum(WALLET, "WALLET_ADDRESS")
else:
    # carrega a partir da chave privada
    if WEB3_AVAILABLE:
        WALLET = Web3.to_checksum_address(Web3(Web3.HTTPProvider(RPC_URL)).eth.account.from_key(PRIVATE_KEY).address)
    else:
        # Fallback: usar endereço padrão para testes
        WALLET = "0x0000000000000000000000000000000000000000"
        logger.warning("Web3 não disponível - usando endereço padrão para WALLET")

WETH = to_checksum(get_env("WETH", default="0x4200000000000000000000000000000000000006"), "WETH")
USDC = to_checksum(get_env("USDC", default="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"), "USDC")

AUTH0_DOMAIN        = get_env("AUTH0_DOMAIN",        required=True)
AUTH0_AUDIENCE      = get_env("AUTH0_AUDIENCE",      required=True)
AUTH0_CLIENT_ID     = get_env("AUTH0_CLIENT_ID",     required=True)
AUTH0_CLIENT_SECRET = get_env("AUTH0_CLIENT_SECRET", required=True)

TELEGRAM_TOKEN = get_env("TELEGRAM_TOKEN", required=True)
TELEGRAM_CHAT  = get_env("TELEGRAM_CHAT_ID", cast=int, default=0)

DRY_RUN            = str_to_bool(get_env("DRY_RUN", default="true"))
DISCOVERY_INTERVAL = get_env("DISCOVERY_INTERVAL", default=3, cast=int)
TRADE_SIZE_ETH     = get_env("TRADE_SIZE_ETH", default=0.1, cast=float)
MIN_LIQ_WETH       = get_env("MIN_LIQ_WETH", default=Decimal("0.5"), cast=Decimal)
TAKE_PROFIT_PCT    = get_env("TAKE_PROFIT_PCT", default=0.2, cast=float)
STOP_LOSS_PCT      = get_env("STOP_LOSS_PCT", default=0.05, cast=float)
EXIT_POLL_INTERVAL = get_env("EXIT_POLL_INTERVAL", default=15, cast=int)

# ─── Newly added vars ──────────────────────────────────────────────────
BASE_TOKENS_RAW   = get_env("BASE_TOKENS", default="", cast=str)
BASE_TOKENS       = [t.strip().lower() for t in BASE_TOKENS_RAW.split(",") if t.strip()]

SLIPPAGE_BPS      = get_env("SLIPPAGE_BPS", default=250, cast=int)
TRAIL_PCT         = get_env("TRAIL_PCT", default=0.08, cast=float)
TX_DEADLINE_SEC   = get_env("TX_DEADLINE_SEC", default=60, cast=int)

DEXES = load_dexes()

config: Dict[str, Any] = {
    "RPC_URL":            RPC_URL,
    "CHAIN_ID":           CHAIN_ID,
    "PRIVATE_KEY":        PRIVATE_KEY,
    "WALLET":             WALLET,
    "WALLET_ADDRESS":     WALLET,
    "WETH":               WETH,
    "USDC":               USDC,
    "AUTH0_DOMAIN":       AUTH0_DOMAIN,
    "AUTH0_AUDIENCE":     AUTH0_AUDIENCE,
    "AUTH0_CLIENT_ID":    AUTH0_CLIENT_ID,
    "AUTH0_CLIENT_SECRET":AUTH0_CLIENT_SECRET,
    "TELEGRAM_TOKEN":     TELEGRAM_TOKEN,
    "TELEGRAM_CHAT_ID":   TELEGRAM_CHAT,
    "DRY_RUN":            DRY_RUN,
    "DISCOVERY_INTERVAL": DISCOVERY_INTERVAL,
    "TRADE_SIZE_ETH":     TRADE_SIZE_ETH,
    "MIN_LIQ_WETH":       MIN_LIQ_WETH,
    "TAKE_PROFIT_PCT":    TAKE_PROFIT_PCT,
    "STOP_LOSS_PCT":      STOP_LOSS_PCT,
    "EXIT_POLL_INTERVAL": EXIT_POLL_INTERVAL,
    "BASE_TOKENS":        BASE_TOKENS,
    "SLIPPAGE_BPS":       SLIPPAGE_BPS,
    "DEFAULT_SLIPPAGE_BPS": SLIPPAGE_BPS,
    "TRAIL_PCT":          TRAIL_PCT,
    "TX_DEADLINE_SEC":    TX_DEADLINE_SEC,
    "DEXES":              DEXES,
}
