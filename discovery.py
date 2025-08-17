import time
import logging
import asyncio
from web3 import Web3
from config import config
from telegram import Bot

# === Instância para notificações ===
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

def notify(msg: str, loop):
    """Envia mensagem para o chat configurado no Telegram."""
    try:
        asyncio.run_coroutine_threadsafe(
            bot_notify.send_message(
                chat_id=config["TELEGRAM_CHAT_ID"],
                text=msg
            ),
            loop
        )
    except Exception as e:
        logger.error(f"Erro ao enviar notificação: {e}")

# 🎯 Evento PairCreated do padrão Uniswap V2
PAIR_CREATED_SIG = Web3.to_hex(
    Web3.keccak(text="PairCreated(address,address,address,uint256)")
)

# ABI mínima para consultar dados do par
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

# 📝 Configuração de log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# 🔁 Controle de execução e status
sniper_active = False
sniper_start_time = None
sniper_pair_count = 0
last_pair_info = None

def safe_checksum(address: str) -> str:
    """Garante que o endereço tenha prefixo 0x e converte para checksum."""
    if not address.startswith("0x"):
        address = "0x" + address
    return Web3.to_checksum_address(address)

def stop_discovery(loop):
    global sniper_active
    sniper_active = False
    logger.info("🛑 Monitoramento interrompido manualmente.")
    notify("🛑 Sniper interrompido manualmente.", loop)

def is_discovery_running():
    return sniper_active

def get_discovery_status():
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
    factory = safe_checksum(config["DEX_FACTORY"])
    logs = web3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": factory,
        "topics": [PAIR_CREATED_SIG]
    })

    found = []
    for log in logs:
        token0 = safe_checksum("0x" + log["topics"][1].hex()[-40:])
        token1 = safe_checksum("0x" + log["topics"][2].hex()[-40:])
        data = log["data"]
        pair_address = safe_checksum("0x" + data[-40:])
        found.append((pair_address, token0, token1))
    return found

def has_min_liquidity(web3, pair_address, weth_address, min_weth_wei):
    pair = web3.eth.contract(address=pair_address, abi=PAIR_ABI)
    r0, r1, _ = pair.functions.getReserves().call()
    t0 = pair.functions.token0().call()
    t1 = pair.functions.token1().call()
    weth_reserve = int(r0) if t0.lower() == weth_address.lower() else int(r1)
    return weth_reserve >= min_weth_wei

def run_discovery(callback_on_pair, loop):
    global sniper_active, sniper_start_time, sniper_pair_count, last_pair_info
    sniper_active = True
    sniper_start_time = time.time()
    sniper_pair_count = 0
    last_pair_info = None

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    last_block = web3.eth.block_number

    # === Lista de tokens-base aceitos (ETH nativo == WETH) ===
    BASE_TOKENS = {
        safe_checksum(config["WETH"]): "WETH",
        safe_checksum(config["usdc"].split("=")[1]): "USDC"
    }

    min_weth_wei = web3.to_wei(config.get("MIN_LIQ_WETH", 1.0), "ether")

    logger.info("🔍 Iniciando monitoramento de novos pares na Base...")
    notify("🔍 Sniper iniciado! Monitorando novos pares na Base...", loop)

    while sniper_active:
        try:
            latest = web3.eth.block_number
            if latest > last_block:
                pairs = scan_new_pairs(web3, last_block + 1, latest)
                last_block = latest

                for pair_addr, token0, token1 in pairs:
                    logger.info(f"📦 Par detectado: {pair_addr} ({token0} / {token1})")

                    if not any(t in BASE_TOKENS for t in (token0, token1)):
                        logger.info("⏭ Ignorado: não contém token-base permitido (WETH, USDC).")
                        continue

                    logger.info(f"🆕 Novo par com token-base encontrado: {pair_addr}")
                    notify(f"🆕 Novo par: {pair_addr}\nTokens: {token0} / {token1}", loop)

                    if has_min_liquidity(web3, pair_addr, safe_checksum(config["WETH"]), min_weth_wei):
                        logger.info("💧 Liquidez mínima atingida — disparando execução...")
                        notify(f"💧 Liquidez mínima atingida no par {pair_addr} — executando sniper!", loop)
                        sniper_pair_count += 1
                        last_pair_info = (pair_addr, token0, token1)
                        callback_on_pair(pair_addr, token0, token1)
                    else:
                        logger.info("⏳ Ainda sem liquidez mínima, ignorando.")
                        notify(f"⏳ Sem liquidez mínima no par {pair_addr}, ignorando.", loop)
        except Exception as e:
            logger.error(f"⚠️ Erro no loop de discovery: {e}", exc_info=True)
            notify(f"⚠️ Erro no loop de discovery: {e}", loop)

        time.sleep(config["INTERVAL"])
