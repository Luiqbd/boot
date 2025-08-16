import time
import logging
from web3 import Web3
from config import config

# Evento PairCreated do padrão Uniswap V2
PAIR_CREATED_SIG = Web3.keccak(text="PairCreated(address,address,address,uint256)").hex()

PAIR_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# 🔁 Controle de execução e status
sniper_active = False
sniper_start_time = None
sniper_pair_count = 0
last_pair_info = None

def stop_discovery():
    """Interrompe o loop de monitoramento."""
    global sniper_active
    sniper_active = False
    logger.info("🛑 Monitoramento interrompido manualmente.")

def is_discovery_running():
    """Retorna True se o sniper estiver ativo."""
    return sniper_active

def get_discovery_status():
    """Retorna informações detalhadas sobre o estado atual do sniper."""
    if not sniper_active:
        return {
            "active": False,
            "text": "🔴 Sniper está parado.",
            "button": None
        }

    uptime = int(time.time() - sniper_start_time)
    minutes, seconds = divmod(uptime, 60)
    status_text = f"🟢 Sniper está ativo há {minutes}m{seconds}s\n"
    status_text += f"🔢 Pares encontrados: {sniper_pair_count}\n"

    if last_pair_info:
        addr, t0, t1 = last_pair_info
        status_text += f"🆕 Último par:\n{addr}\n🧬 Tokens:\n{t0[:6]}... / {t1[:6]}..."
    else:
        status_text += "🆕 Nenhum par encontrado ainda."

    return {
        "active": True,
        "text": status_text,
        "button": "🛑 Parar sniper"
    }

def scan_new_pairs(web3, from_block: int, to_block: int):
    factory = Web3.to_checksum_address(config["DEX_FACTORY"])
    logs = web3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address
