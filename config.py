import os
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from web3 import Web3

logger = logging.getLogger(__name__)
load_dotenv()

# ---------------------------------------------------
# Helpers de leitura e validação de ambiente
# ---------------------------------------------------

def str_to_bool(val: Union[str, bool]) -> bool:
    """
    Converte string True/False, '1', 'y', 'yes' em boolean.
    """
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "t", "yes", "y"}


def get_env(
    key: str,
    default: Optional[Any] = None,
    cast: Any = str,
    required: bool = False
) -> Any:
    """
    Lê variável de ambiente, converte via 'cast' e aplica valor default.
    Se required=True e valor vazio, dispara RuntimeError.
    """
    raw = os.getenv(key, None)
    if raw is None or raw.strip() == "":
        if required and default is None:
            raise RuntimeError(f"Variável obrigatória '{key}' não informada")
        raw = default
    try:
        return cast(raw) if raw is not None else raw
    except Exception as e:
        raise RuntimeError(f"Falha ao converter '{key}'={raw}: {e}")


def normalize_private_key(pk: str) -> str:
    """
    Valida PRIVATE_KEY no formato hex sem '0x' ou com. Retorna sem prefixo.
    """
    if not pk:
        raise ValueError("PRIVATE_KEY vazia")
    key = pk.lower().removeprefix("0x")
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("PRIVATE_KEY inválida")
    return key


def to_checksum(addr: str, name: str) -> str:
    """
    Valida e converte endereço Ethereum a EIP-55 checksum.
    """
    if not Web3.is_address(addr):
        raise ValueError(f"Endereço '{name}' inválido: {addr}")
    return Web3.to_checksum_address(addr)


# ---------------------------------------------------
# Configurações de DEXes
# ---------------------------------------------------

@dataclass(frozen=True)
class DexConfig:
    name: str
    factory: str
    router: str
    type: str  # 'v2' ou 'v3'


def load_dexes() -> List[DexConfig]:
    """
    Carrega e valida DEXes a partir de variáveis de ambiente.
    Cada entrada deve seguir o padrão:
      DEX_{N}_NAME, DEX_{N}_FACTORY, DEX_{N}_ROUTER, DEX_{N}_TYPE
    """
    dexes: List[DexConfig] = []
    idx = 1
    while True:
        prefix = f"DEX_{idx}_"
        name = os.getenv(prefix + "NAME")
        if not name:
            break
        factory = to_checksum(
            get_env(prefix + "FACTORY", required=True),
            f"{name} factory"
        )
        router = to_checksum(
            get_env(prefix + "ROUTER", required=True),
            f"{name} router"
        )
        dtype = get_env(prefix + "TYPE", default="v2").lower()
        if dtype not in ("v2", "v3"):
            raise ValueError(f"Tipo inválido para {name}: {dtype}")
        dexes.append(DexConfig(name=name, factory=factory, router=router, type=dtype))
        idx += 1
    if not dexes:
        logger.warning("Nenhuma DEX configurada. Verifique variáveis DEX_1_…")
    return dexes


# ---------------------------------------------------
# Carregamento principal de parâmetros
# ---------------------------------------------------

# RPC e Chain
RPC_URL    = get_env("RPC_URL", default="https://mainnet.base.org")
CHAIN_ID   = get_env("CHAIN_ID", default=8453, cast=int)

# Carteira e chaves
PRIVATE_KEY = normalize_private_key(get_env("PRIVATE_KEY", required=True))
WALLET      = get_env("WALLET_ADDRESS", default=None)
if WALLET:
    WALLET = to_checksum(WALLET, "WALLET_ADDRESS")

# Tokens padrão
WETH = to_checksum(
    get_env("WETH", default="0x4200000000000000000000000000000000000006"),
    "WETH"
)
USDC = to_checksum(
    get_env("USDC", default="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"),
    "USDC"
)

# Telegram
TELEGRAM_TOKEN = get_env("TELEGRAM_TOKEN", required=True)
TELEGRAM_CHAT  = get_env("TELEGRAM_CHAT_ID", cast=int, default=0)

# Operação
DRY_RUN          = str_to_bool(get_env("DRY_RUN", default="true"))
INTERVAL         = get_env("INTERVAL", default=3, cast=int)
DEFAULT_SLIPPAGE = get_env("SLIPPAGE_BPS", default=50, cast=int)
TX_DEADLINE_SEC  = get_env("TX_DEADLINE_SEC", default=300, cast=int)
MIN_LIQ_WETH     = get_env("MIN_LIQ_WETH", default=Decimal("0.5"), cast=Decimal)
DISCOVERY_INTERVAL = get_env("DISCOVERY_INTERVAL", default=3, cast=int)
PAIR_DUP_INTERVAL  = get_env("PAIR_DUP_INTERVAL", default=5, cast=int)

# APIs externas
ETHERSCAN_API_KEY = get_env("ETHERSCAN_API_KEY", default="")

# Lista de DEXes
DEXES = load_dexes()

# ---------------------------------------------------
# Validação final de sanidade
# ---------------------------------------------------

def validate_cfg() -> None:
    assert RPC_URL.startswith("http"), "RPC_URL deve ser URL válida"
    assert CHAIN_ID > 0, "CHAIN_ID deve ser inteiro > 0"
    Web3.to_checksum_address(WETH)
    Web3.to_checksum_address(USDC)
    for dex in DEXES:
        Web3.to_checksum_address(dex.factory)
        Web3.to_checksum_address(dex.router)

validate_cfg()


# ---------------------------------------------------
# Exposição do dicionário de config
# ---------------------------------------------------

config: Dict[str, Any] = {
    "RPC_URL": RPC_URL,
    "CHAIN_ID": CHAIN_ID,
    "PRIVATE_KEY": PRIVATE_KEY,
    "WALLET": WALLET,
    "WETH": WETH,
    "USDC": USDC,
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT,
    "DRY_RUN": DRY_RUN,
    "INTERVAL": INTERVAL,
    "DEFAULT_SLIPPAGE_BPS": DEFAULT_SLIPPAGE,
    "TX_DEADLINE_SEC": TX_DEADLINE_SEC,
    "MIN_LIQ_WETH": MIN_LIQ_WETH,
    "DISCOVERY_INTERVAL": DISCOVERY_INTERVAL,
    "PAIR_DUP_INTERVAL": PAIR_DUP_INTERVAL,
    "ETHERSCAN_API_KEY": ETHERSCAN_API_KEY,
    "DEXES": DEXES,
}

# Debug opcional
if str_to_bool(get_env("DEBUG_CONFIG", default="false")):
    logger.debug("Config carregada: %s", config)
