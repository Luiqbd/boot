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
        return "⛔ Sniper está parado."

    uptime = int(time.time() - sniper_start_time)
    minutes, seconds = divmod(uptime, 60)
    status = f"🔄 Sniper está ativo há {minutes}m{seconds}s\n"
    status += f"🔢 Pares encontrados: {sniper_pair_count}\n"
    if last_pair_info:
        addr, t0, t1 = last_pair_info
        status += f"🆕 Último par: {addr}\n🧬 Tokens: {t0[:6]}... / {t1[:6]}..."
    else:
        status += "🆕 Nenhum par encontrado ainda."
    return status

def scan_new_pairs(web3, from_block: int, to_block: int):
    factory = Web3.to_checksum_address(config["DEX_FACTORY"])
    logs = web3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": factory,
        "topics": [PAIR_CREATED_SIG]
    })

    found = []
    for log in logs:
        token0 = Web3.to_checksum_address("0x" + log["topics"][1].hex()[-40:])
        token1 = Web3.to_checksum_address("0x" + log["topics"][2].hex()[-40:])
        data = log["data"]
        pair_address = Web3.to_checksum_address("0x" + data[-40:])
        found.append((pair_address, token0, token1))
    return found

def has_min_liquidity(web3, pair_address, weth_address, min_weth_wei):
    pair = web3.eth.contract(address=pair_address, abi=PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    t0 = pair.functions.token0().call()
    t1 = pair.functions.token1().call()

    weth_reserve = int(r0) if t0.lower() == weth_address.lower() else int(r1)
    return weth_reserve >= min_weth_wei

def run_discovery(callback_on_pair):
    global sniper_active, sniper_start_time, sniper_pair_count, last_pair_info
    sniper_active = True
    sniper_start_time = time.time()
    sniper_pair_count = 0
    last_pair_info = None

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    last_block = web3.eth.block_number

    weth = Web3.to_checksum_address(config["WETH"])
    min_weth_wei = web3.to_wei(config.get("MIN_LIQ_WETH", 1.0), "ether")

    logger.info("🔍 Iniciando monitoramento de novos pares na Base...")

    while sniper_active:
        latest = web3.eth.block_number
        if latest > last_block:
            pairs = scan_new_pairs(web3, last_block + 1, latest)
            last_block = latest

            for pair_addr, token0, token1 in pairs:
                if weth not in (token0, token1):
                    continue

                logger.info(f"🆕 Novo par encontrado: {pair_addr} ({token0} / {token1})")

                if has_min_liquidity(web3, pair_addr, weth, min_weth_wei):
                    logger.info("💧 Liquidez mínima atingida — disparando execução...")
                    sniper_pair_count += 1
                    last_pair_info = (pair_addr, token0, token1)
                    callback_on_pair(pair_addr, token0, token1)
                else:
                    logger.info("⏳ Ainda sem liquidez mínima, ignorando.")
        time.sleep(config["INTERVAL"])
