import os

def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

config = {
    "PYTHON_VERSION": os.getenv("PYTHON_VERSION", "3.10.12"),
    "RPC_URL": os.getenv("RPC_URL"),
    "PRIVATE_KEY": os.getenv("PRIVATE_KEY"),
    "CHAIN_ID": int(os.getenv("CHAIN_ID", "8453")),
    "DEX_ROUTER": os.getenv("DEX_ROUTER"),
    "WETH": os.getenv("WETH"),
    "DEFAULT_SLIPPAGE_BPS": int(os.getenv("SLIPPAGE_BPS", "50")),
    "TX_DEADLINE_SEC": int(os.getenv("TX_DEADLINE_SEC", "300")),
    "INTERVAL": int(os.getenv("INTERVAL", "10")),
    "DRY_RUN": str_to_bool(os.getenv("DRY_RUN", "true")),
    "TELEGRAM_TOKEN": os.getenv("TELEGRAM_TOKEN"),
    "TELEGRAM_CHAT_ID": int(os.getenv("TELEGRAM_CHAT_ID", "6061309909")),  # ← seu ID aqui
}

# Checagens rápidas (opcional)
def _require(name: str, cond: bool):
    if not cond:
        raise ValueError(f"Config inválida: {name}")

_require("RPC_URL", bool(config["RPC_URL"]))
_require("PRIVATE_KEY", bool(config
