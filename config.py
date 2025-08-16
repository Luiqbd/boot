import os
from web3 import Web3

def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    """Remove prefixo 0x se existir e valida formato."""
    if not pk:
        raise ValueError("Chave privada não informada")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    # Validação: exatamente 64 caracteres hexadecimais
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("Chave privada inválida: formato incorreto")
    return pk

# Carrega variáveis de ambiente
raw_private_key = os.getenv("PRIVATE_KEY")
PRIVATE_KEY = normalize_private_key(raw_private_key)

config = {
    "PYTHON_VERSION": os.getenv("PYTHON_VERSION", "3.10.12"),

    # RPC e credenciais
    "RPC_URL": os.getenv("RPC_URL", "https://mainnet.base.org"),
    "PRIVATE_KEY": PRIVATE_KEY,  # chave normalizada
    "CHAIN_ID": int(os.getenv("CHAIN_ID", "8453")),

    # Aerodrome (Base) — V2-like AMM
    "DEX_ROUTER": os.getenv("DEX_ROUTER", "0xcF77a3D4A6f1C6a7D5cb06B52F474BeCC5123e29"),
    "DEX_FACTORY": os.getenv("DEX_FACTORY", "0x327Df1e6de05895d2ab08513aaDD9313Fe505d86"),

    # WETH oficial na Base
    "WETH": os.getenv("WETH", Web3.to_checksum_address("0x4200000000000000000000000000000000000006")),

    # Execução
    "DEFAULT_SLIPPAGE_BPS": int(os.getenv("SLIPPAGE_BPS", "1200")),  # 12% padrão
    "TX_DEADLINE_SEC": int(os.getenv("TX_DEADLINE_SEC", "45")),
    "INTERVAL": int(os.getenv("INTERVAL", "3")),  # tempo entre scans (s)
    "DRY_RUN": str_to_bool(os.getenv("DRY_RUN", "true")),

    # Telegram (opcional)
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
    "TELEGRAM_CHAT_ID": int(os.getenv("TELEGRAM_CHAT_ID", "0")),
}

# Checagens
def _require(name: str, cond: bool):
    if not cond:
        raise ValueError(f"Config inválida: {name}")

_require("RPC_URL", bool(config["RPC_URL"]))
_require("PRIVATE_KEY", bool(config["PRIVATE_KEY"]))
_require("DEX_ROUTER length", config["DEX_ROUTER"] and len(config["DEX_ROUTER"]) == 42)
_require("DEX_FACTORY length", config["DEX_FACTORY"] and len(config["DEX_FACTORY"]) == 42)
_require("WETH length", config["WETH"] and len(config["WETH"]) == 42)
_require("CHAIN_ID", config["CHAIN_ID"] == 8453)

# Log opcional para debug (com máscara de segurança)
print("Signer Address:", Web3().eth.account.from_key(PRIVATE_KEY).address)
