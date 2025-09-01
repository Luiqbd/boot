import os
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Dict, Any

from web3 import Web3

logger = logging.getLogger(__name__)


def str_to_bool(v: str) -> bool:
    """
    Converte string para boolean, aceitando valores como
    '1', 'true', 'yes', 'y' (case-insensitive).
    """
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}


def get_env(key: str, default: Any = None, required: bool = False) -> str:
    """
    Lê VAR de ambiente, usando default se fornecido. Se required=True
    e não existir, dispara RuntimeError.
    """
    val = os.getenv(key, default)
    if required and (val is None or str(val).strip() == ""):
        raise RuntimeError(f"Variável obrigatória '{key}' não informada")
    return str(val).strip()


def normalize_private_key(pk: str) -> str:
    """Valida e remove prefixo '0x' de uma chave privada."""
    if not pk:
        raise ValueError("PRIVATE_KEY vazia")
    pk = pk.lower().removeprefix("0x")
    if len(pk) != 64 or any(c not in "0123456789abcdef" for c in pk):
        raise ValueError("PRIVATE_KEY inválida: formato ou tamanho incorreto")
    return pk


def checksum_addr(env_key: str, default: str, name: str) -> str:
    """
    Lê endereço via env_key ou usa default. Retorna EIP-55 checksum,
    ou dispara ValueError se inválido.
    """
    raw = get_env(env_key, default, required=default is None)
    if not Web3.is_address(raw):
        raise ValueError(f"Endereço '{name}' inválido ({raw})")
    return Web3.to_checksum_address(raw)


@dataclass(frozen=True)
class DexConfig:
    name: str
    factory: str
    router: str
    type: str  # 'v2' ou 'v3'


def load_dexes() -> List[DexConfig]:
    """
    Retorna a lista de DEXes configuradas, validando
    cada par de factory/router.
    """
    definitions = [
        ("Aerodrome V2", "AERO_V2_FACTORY", "AERO_V2_ROUTER", "v2"),
        ("Aerodrome V3", "AERO_V3_FACTORY", "AERO_V3_ROUTER", "v3"),
        ("Uniswap V2",    "UNI_V2_FACTORY",  "UNI_V2_ROUTER",  "v2"),
        ("Uniswap V3",    "UNI_V3_FACTORY",  "UNI_V3_ROUTER",  "v3"),
        ("BaseSwap V2",   "BASE_V2_FACTORY", "BASE_V2_ROUTER", "v2"),
        ("BaseSwap V3",   "BASE_V3_FACTORY", "BASE_V3_ROUTER", "v3"),
        ("SushiSwap",     "SUSHI_FACTORY",   "SUSHI_ROUTER",   "v2"),
    ]
    dexes: List[DexConfig] = []
    for name, f_key, r_key, dtype in definitions:
        factory = checksum_addr(f_key, None, f"{name} factory")
        router  = checksum_addr(r_key, None, f"{name} router")
        dexes.append(DexConfig(name=name, factory=factory, router=router, type=dtype))
    return dexes


# ---------------------------------------------------
# Carregamento principal
# ---------------------------------------------------

# 1. Chave privada
PRIVATE_KEY = normalize_private_key(get_env("PRIVATE_KEY", required=True))

# 2. Tokens oficiais
WETH = checksum_addr("WETH", "0x4200000000000000000000000000000000000006", "WETH")
USDC = checksum_addr("USDC", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "USDC")

# 3. DEXes
DEXES = load_dexes()

# 4. Config geral
config: Dict[str, Any] = {
    "RPC_URL":           get_env("RPC_URL", "https://mainnet.base.org"),
    "CHAIN_ID":          int(get_env("CHAIN_ID", "8453")),
    "PRIVATE_KEY":       PRIVATE_KEY,
    "WETH":              WETH,
    "USDC":              USDC,
    "DEFAULT_SLIPPAGE_BPS": int(get_env("SLIPPAGE_BPS", "50")),
    "TX_DEADLINE_SEC":      int(get_env("TX_DEADLINE_SEC", "45")),
    "INTERVAL":             int(get_env("INTERVAL", "3")),
    "DRY_RUN":              str_to_bool(get_env("DRY_RUN", "true")),
    "TELEGRAM_TOKEN":       get_env("TELEGRAM_TOKEN", None, required=True),
    "TELEGRAM_CHAT_ID":     int(get_env("TELEGRAM_CHAT_ID", "0")),
    "ETHERSCAN_API_KEY":    get_env("ETHERSCAN_API_KEY", ""),
    "DEXES":                DEXES,
}


def validate_config() -> None:
    """
    Executa validações de sanidade do config; dispara ValueError se algo não bater.
    """
    assert config["RPC_URL"].startswith("http"), "RPC_URL deve ser uma URL válida"
    assert isinstance(config["CHAIN_ID"], int) and config["CHAIN_ID"] > 0
    assert Web3.is_address(config["WETH"])
    assert Web3.is_address(config["USDC"])
    for dex in config["DEXES"]:
        if not Web3.is_address(dex.factory) or not Web3.is_address(dex.router):
            raise ValueError(f"Endereço inválido em DEX {dex.name}")


validate_config()

# Debug opcional
if str_to_bool(get_env("DEBUG_CONFIG", "false")):
    logger.debug("Config carregada: %s", config)
