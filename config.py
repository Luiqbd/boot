import os
from web3 import Web3

# ---------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------
def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("Chave privada não informada no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError(f"Chave privada inválida ({pk[:4]}...): formato ou tamanho incorreto")
    return pk

def checksum_addr(addr_env: str, default: str = None) -> str:
    if not addr_env and default:
        addr_env = default
    return Web3.to_checksum_address(addr_env)

# ---------------------------------------------------
# Carrega e valida chave privada
# ---------------------------------------------------
raw_private_key = os.getenv("PRIVATE_KEY")
try:
    PRIVATE_KEY = normalize_private_key(raw_private_key)
except ValueError as e:
    raise RuntimeError(f"Erro ao processar PRIVATE_KEY: {e}")

# ---------------------------------------------------
# Tokens oficiais na Base Mainnet
# ---------------------------------------------------
WETH = checksum_addr(os.getenv("WETH"), "0x4200000000000000000000000000000000000006")
USDC = checksum_addr(os.getenv("USDC"), "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# ---------------------------------------------------
# Lista de DEX monitoradas — endereços oficiais Base
# ---------------------------------------------------
DEXES = [
    {
        "name": "Aerodrome V2",
        "factory": checksum_addr(os.getenv("AERO_V2_FACTORY"), "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86"),
        "router": checksum_addr(os.getenv("AERO_V2_ROUTER"), "0xcF083Be4164828f00cAE704EC15a36D711491284"),
        "type": "v2"
    },
    {
        "name": "Aerodrome V3",
        "factory": checksum_addr(os.getenv("AERO_V3_FACTORY"), "0x420dd381b31aef6683db6b90208442878ebf0f8d"),
        "router": checksum_addr(os.getenv("AERO_V3_ROUTER"), "0x14eBb7fc750F1107E6d36fB31A0c6B0f7F73B09F"),
        "type": "v3"
    },
    {
        "name": "Uniswap V2",
        "factory": checksum_addr(os.getenv("UNI_V2_FACTORY"), "0x9C454510848906FDDc846607E4baa27Ca999FBB6"),
        "router": checksum_addr(os.getenv("UNI_V2_ROUTER"), "0x2626664c2603336E57B271c5C0b26F421741e481"),
        "type": "v2"
    },
    {
        "name": "Uniswap V3",
        "factory": checksum_addr(os.getenv("UNI_V3_FACTORY"), "0x33128a8fC17869897Dce68Ed026d694621f6FDfD"),
        "router": checksum_addr(os.getenv("UNI_V3_ROUTER"), "0x2626664c2603336E57B271c5C0b26F421741e481"),
        "type": "v3"
    },
    {
        "name": "BaseSwap V2",
        "factory": checksum_addr(os.getenv("BASE_V2_FACTORY"), "0x06e0feb0d74106c7ada8497754074d222ec6e8ef"),
        "router": checksum_addr(os.getenv("BASE_V2_ROUTER"), "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86"),
        "type": "v2"
    },
    {
        "name": "BaseSwap V3",
        "factory": checksum_addr(os.getenv("BASE_V3_FACTORY"), "0x47989441fD3A19774f8aF9F21614c83Bfb4b0775"),
        "router": checksum_addr(os.getenv("BASE_V3_ROUTER"), "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86"),
        "type": "v3"
    },
    {
        "name": "SushiSwap",
        "factory": checksum_addr(os.getenv("SUSHI_FACTORY"), "0x71524b4f93c58fcb2e0f0c5e2ada8f350b9a0212"),
        "router": checksum_addr(os.getenv("SUSHI_ROUTER"), "0x044b75f554b886A065b9567891e45c79542d7357"),
        "type": "v2"
    }
]

# ---------------------------------------------------
# Config final
# ---------------------------------------------------
config = {
    "PYTHON_VERSION": os.getenv("PYTHON_VERSION", "3.10.12"),

    # Blockchain
    "RPC_URL": os.getenv("RPC_URL", "https://mainnet.base.org"),
    "PRIVATE_KEY": PRIVATE_KEY,
    "CHAIN_ID": int(os.getenv("CHAIN_ID", "8453")),

    # Tokens base
    "WETH": WETH,
    "USDC": USDC,

    # Execução
    "DEFAULT_SLIPPAGE_BPS": int(os.getenv("SLIPPAGE_BPS", "50")),
    "TX_DEADLINE_SEC": int(os.getenv("TX_DEADLINE_SEC", "45")),
    "INTERVAL": int(os.getenv("INTERVAL", "3")),
    "DRY_RUN": str_to_bool(os.getenv("DRY_RUN", "true")),

    # Telegram
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
    "TELEGRAM_CHAT_ID": int(os.getenv("TELEGRAM_CHAT_ID", "0")),

    # Etherscan
    "ETHERSCAN_API_KEY": os.getenv("ETHERSCAN_API_KEY"),

    # DEX
    "DEXES": DEXES
}

# ---------------------------------------------------
# Validações
# ---------------------------------------------------
def _require(name: str, cond: bool):
    if not cond:
        raise ValueError(f"Config inválida: {name} — valor atual: {config.get(name)}")

_require("RPC_URL", bool(config["RPC_URL"]))
_require("PRIVATE_KEY", bool(config["PRIVATE_KEY"]))
_require("WETH", isinstance(config["WETH"], str) and len(config["WETH"]) == 42)
_require("USDC", isinstance(config["USDC"], str) and len(config["USDC"]) == 42)
_require("CHAIN_ID", config["CHAIN_ID"] == 8453)

for dex in config["DEXES"]:
    if not (isinstance(dex["factory"], str) and len(dex["factory"]) == 42):
        raise ValueError(f"Factory inválida em {dex['name']}")
    if not (isinstance(dex["router"], str) and len(dex["router"]) == 42):
        raise ValueError(f"Router inválido em {dex['name']}")

# ---------------------------------------------------
# Debug opcional
# ---------------------------------------------------
if str_to_bool(os.getenv("DEBUG_CONFIG", "false")):
    signer_addr = Web3().eth.account.from_key(PRIVATE_KEY).address
    print(f"Signer Address: {signer_addr}")
    print(f"WETH: {WETH}")
    print(f"USDC: {USDC}")
    for dex in config["DEXES"]:
        print(f"{dex['name']} → Factory: {dex['factory']} | Router: {dex['router']}")
