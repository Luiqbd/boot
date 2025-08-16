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

def scan_new_pairs(web3, from_block: int, to_block: int):
    """Busca eventos PairCreated no intervalo de blocos."""
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
        data = log["data"]  # pair address está no data no V2
        pair_address = Web3.to_checksum_address("0x" + data[-40:])
        found.append((pair_address, token0, token1))
    return found

def has_min_liquidity(web3, pair_address, weth_address, min_weth_wei):
    """Confere se o par já tem a liquidez mínima em WETH."""
    pair = web3.eth.contract(address=pair_address, abi=PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    t0 = pair.functions.token0().call()
    t1 = pair.functions.token1().call()

    if t0.lower() == weth_address.lower():
        weth_reserve = int(r0)
    else:
        weth_reserve = int(r1)

    return weth_reserve >= min_weth_wei

def run_discovery(callback_on_pair):
    """Loop contínuo para encontrar novos pares e acionar callback."""
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    last_block = web3.eth.block_number

    weth = Web3.to_checksum_address(config["WETH"])
    min_weth_wei = web3.to_wei(config.get("MIN_LIQ_WETH", 1.0), "ether")

    logger.info("🔍 Iniciando monitoramento de novos pares na Base...")

    while True:
        latest = web3.eth.block_number
        if latest > last_block:
            pairs = scan_new_pairs(web3, last_block + 1, latest)
            last_block = latest

            for pair_addr, token0, token1 in pairs:
                # Apenas pares que envolvem WETH
                if weth not in (token0, token1):
                    continue

                logger.info(f"🆕 Novo par encontrado: {pair_addr} ({token0} / {token1})")

                if has_min_liquidity(web3, pair_addr, weth, min_weth_wei):
                    logger.info("💧 Liquidez mínima atingida — disparando execução...")
                    callback_on_pair(pair_addr, token0, token1)
                else:
                    logger.info("⏳ Ainda sem liquidez mínima, ignorando.")
        time.sleep(config["INTERVAL"])
