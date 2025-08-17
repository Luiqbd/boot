import os
from web3 import Web3

def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("Chave privada não informada no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError(f"Chave privada inválida ({pk[:4]}...): formato incorreto ou tamanho incorreto")
    return pk

# Carrega e valida PRIVATE_KEY
raw_private_key = os.getenv("PRIVATE_KEY")
try:
    PRIVATE_KEY = normalize_private_key(raw_private_key)
except ValueError as e:
    raise RuntimeError(f"Erro ao processar PRIVATE_KEY: {e}")

def checksum_addr(addr_env: str, default: str = None) -> str:
    if not addr_env and default:
        addr_env = default
    return Web3.to_checksum_address(addr_env)

WETH = checksum_addr(os.getenv("WETH"), "0x4200000000000000000000000000000000000006")

config = {
    "PYTHON_VERSION": os.getenv("PYTHON_VERSION", "3.10.12"),

    # RPC e credenciais
    "RPC_URL": os.getenv("RPC_URL", "https://mainnet.base.org"),
    "PRIVATE_KEY": PRIVATE_KEY,
    "CHAIN_ID": int(os.getenv("CHAIN_ID", "8453")),

    # Aerodrome (Base)
    "DEX_ROUTER": checksum_addr(os.getenv("DEX_ROUTER"), "0xcF77a3D4A6f1C6a7D5cb06B52F474BeCC5123e29"),
    "DEX_FACTORY": checksum_addr(os.getenv("DEX_FACTORY"), "0x327Df1e6de05895d2ab08513aaDD9313Fe505d86"),

    # WETH oficial
    "WETH": WETH,

    # Execução
    "DEFAULT_SLIPPAGE_BPS": int(os.getenv("SLIPPAGE_BPS", "1200")),
    "TX_DEADLINE_SEC": int(os.getenv("TX_DEADLINE_SEC", "45")),
    "INTERVAL": int(os.getenv("INTERVAL", "3")),
    "DRY_RUN": str_to_bool(os.getenv("DRY_RUN", "true")),

    # Telegram
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
    "TELEGRAM_CHAT_ID": int(os.getenv("TELEGRAM_CHAT_ID", "0")),
}

# Validações básicas
def _require(name: str, cond: bool):
    if not cond:
        raise ValueError(f"Config inválida: {name} — valor atual: {config.get(name)}")

_require("RPC_URL", bool(config["RPC_URL"]))
_require("PRIVATE_KEY", bool(config["PRIVATE_KEY"]))
_require("DEX_ROUTER", isinstance(config["DEX_ROUTER"], str) and len(config["DEX_ROUTER"]) == 42)
_require("DEX_FACTORY", isinstance(config["DEX_FACTORY"], str) and len(config["DEX_FACTORY"]) == 42)
_require("WETH", isinstance(config["WETH"], str) and len(config["WETH"]) == 42)
_require("CHAIN_ID", config["CHAIN_ID"] == 8453)

# Debug opcional
if str_to_bool(os.getenv("DEBUG_CONFIG", "false")):
    signer_addr = Web3().eth.account.from_key(PRIVATE_KEY).address
    print(f"Signer Address: {signer_addr}")
