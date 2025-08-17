import time
import logging
import asyncio
from web3 import Web3
from config import config
from telegram import Bot

# === Instância para notificações ===
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

# === Variável global de simulação de PnL ===
pnl_total = 0.0

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
    status_text += f"💹 PnL simulado: {pnl_total:.4f} WETH\n"

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

# === Callback default ===
def default_callback_on_pair(pair_addr, token0, token1):
    global pnl_total
    if config.get("DRY_RUN", True):
        simulated_profit = 0.01
        pnl_total += simulated_profit
        logger.info(f"[SIMULAÇÃO] Par {pair_addr} -> Lucro {simulated_profit:.4f} WETH (PnL total: {pnl_total:.4f})")
    else:
        logger.info(f"[REAL] Executando compra no par {pair_addr}")
        execute_trade(pair_addr, token0, token1, amount_in_wei=Web3.to_wei(0.1, 'ether'))

# === Execução real ===
def execute_trade(pair_addr, token0, token1, amount_in_wei):
    """Execução real usando Router V2 (tudo vem do config, que já lê do Render)."""
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        router = web3.eth.contract(
            address=Web3.to_checksum_address(config["ROUTER_ADDRESS"]),
            abi=[{
                "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
                "type": "function",
                "stateMutability": "payable",
                "inputs": [
                    {"name": "amountOutMin", "type": "uint256"},
                    {"name": "path", "type": "address[]"},
                    {"name": "to", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                ],
                "outputs": []
            }]
        )

        weth_addr = Web3.to_checksum_address(config["WETH"])
        path = [weth_addr, token1] if token0.lower() == weth_addr.lower() else [weth_addr, token0]

        tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
            0,
            path,
            Web3.to_checksum_address(config["WALLET_ADDRESS"]),
            int(time.time()) + 60
        ).build_transaction({
            "from": Web3.to_checksum_address(config["WALLET_ADDRESS"]),
            "value": amount_in_wei,
            "gas": 300000,
            "gasPrice": web3.to_wei("5", "gwei"),
            "nonce": web3.eth.get_transaction_count(Web3.to_checksum_address(config["WALLET_ADDRESS"]))
        })

        signed_tx = web3.eth.account.sign_transaction(tx, private_key=config["PRIVATE_KEY"])
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        logger.info(f"✅ Compra enviada! Hash: {web3.to_hex(tx_hash)}")
        notify(f"✅ Compra enviada! Hash: {web3.to_hex(tx_hash)}", asyncio.get_event_loop())
        return tx_hash

    except Exception as e:
        logger.error(f"❌ Erro na execução real: {e}", exc_info=True)
        notify(f"❌ Erro na execução real: {e}", asyncio.get_event_loop())
        return None

# === Monitoramento ===
def run_discovery(callback_on_pair, loop):
    global sniper_active, sniper_start_time, sniper_pair_count, last_pair_info, pnl_total
    sniper_active = True
    sniper_start_time = time.time()
    sniper_pair_count = 0
    last_pair_info = None
    pnl_total = 0.0

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    last_block = web3.eth.block_number

    BASE_TOKENS = {
        safe_checksum(config["WETH"]): "WETH",
        safe_checksum(config["USDC"]): "USDC"
    }

    min_weth_wei = web3.to_wei(config.get("MIN_LIQ_WETH", 1.0), "ether")

    logger.info("🔍 Iniciando monitoramento de novos pares...")
    notify("🔍 Sniper iniciado! Monitorando novos pares...", loop)

    while sniper_active:
        try:
            latest = web3.eth.block_number
            if latest >
