import time
import logging
import asyncio
import inspect
from web3 import Web3
from config import config
from telegram import Bot

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("discovery")

# -----------------------------------------------------------------------------
# Notificações
# -----------------------------------------------------------------------------
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])
pnl_total = 0.0
notify_loop = None  # loop padrão para notify quando não for passado explicitamente

def notify(msg: str, loop=None):
    """Envia mensagem para o chat configurado no Telegram (thread-safe via loop)."""
    try:
        target_loop = loop or notify_loop
        if target_loop is None:
            target_loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            bot_notify.send_message(
                chat_id=config["TELEGRAM_CHAT_ID"],
                text=msg
            ),
            target_loop
        )
    except Exception as e:
        logger.error(f"Erro ao enviar notificação: {e}")

# -----------------------------------------------------------------------------
# Constantes e ABIs mínimos
# -----------------------------------------------------------------------------
PAIR_CREATED_SIG = Web3.to_hex(Web3.keccak(text="PairCreated(address,address,address,uint256)"))   # V2
POOL_CREATED_SIG = Web3.to_hex(Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)"))  # V3

# ABI mínima para ler reservas e tokens em pares V2
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

# -----------------------------------------------------------------------------
# Estado do discovery
# -----------------------------------------------------------------------------
sniper_active = False
sniper_start_time = None
sniper_pair_count = 0
last_pair_info = None  # (pair_address, token0, token1)

def safe_checksum(address: str) -> str:
    """Normaliza e aplica checksum a um endereço."""
    if isinstance(address, bytes):
        address = address.hex()
    if not str(address).startswith("0x"):
        address = "0x" + str(address)
    return Web3.to_checksum_address(address)

def stop_discovery(loop):
    """Interrompe o monitoramento."""
    global sniper_active
    sniper_active = False
    logger.info("🛑 Monitoramento interrompido manualmente.")
    notify("🛑 Sniper interrompido manualmente.", loop)

def is_discovery_running():
    return sniper_active

def get_discovery_status():
    """Retorna um resumo textual do estado atual do discovery."""
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

def has_min_liquidity_v2(web3, pair_address, weth_address, min_weth_wei):
    """Verifica liquidez mínima apenas para contratos V2 (getReserves)."""
    try:
        pair = web3.eth.contract(address=pair_address, abi=PAIR_ABI)
        r0, r1, _ = pair.functions.getReserves().call()
        t0 = pair.functions.token0().call()
        t1 = pair.functions.token1().call()
        weth_reserve = int(r0) if t0.lower() == weth_address.lower() else int(r1)
        return weth_reserve >= min_weth_wei
    except Exception as e:
        # Em V3, essa chamada não existe; para V2, qualquer falha registra aviso.
        logger.warning(f"Erro ao verificar liquidez no par {pair_address}: {e}")
        return False

# Callback exemplo caso nenhum seja fornecido
def default_callback_on_pair(dex_info, pair_addr, token0, token1):
    global pnl_total
    simulated_profit = 0.01
    pnl_total += simulated_profit
    logger.info(f"[SIM] [{dex_info['name']}] {pair_addr} -> Lucro {simulated_profit:.4f} WETH (PnL total: {pnl_total:.4f})")

# -----------------------------------------------------------------------------
# Loop principal de discovery (multi-DEX)
# -----------------------------------------------------------------------------
def run_discovery(callback_on_pair, loop):
    """
    callback_on_pair: função chamada como callback_on_pair(dex_info, pair_addr, token0, token1)
                      pode ser síncrona OU assíncrona (coroutine).
    loop: loop de eventos para notificações e callbacks assíncronos.
    """
    global sniper_active, sniper_start_time, sniper_pair_count, last_pair_info, pnl_total, notify_loop
    notify_loop = loop

    sniper_active = True
    sniper_start_time = time.time()
    sniper_pair_count = 0
    last_pair_info = None
    pnl_total = 0.0

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))

    # Guarda o último bloco processado por DEX
    last_blocks = {}
    current_block = web3.eth.block_number
    for dex in config["DEXES"]:
        last_blocks[dex["name"]] = current_block

    BASE_TOKENS = {
        safe_checksum(config["WETH"]): "WETH",
        safe_checksum(config["USDC"]): "USDC"
    }
    min_weth_wei = Web3.to_wei(config.get("MIN_LIQ_WETH", 1.0), "ether")
    interval = int(config.get("INTERVAL", 3))

    logger.info("🔍 Iniciando monitoramento de novos pares em todas as DEX...")
    notify("🔍 Sniper iniciado! Monitorando novos pares em todas as DEX...", loop)

    while sniper_active:
        try:
            latest_block = web3.eth.block_number

            for dex in config["DEXES"]:
                from_block = last_blocks[dex["name"]] + 1
                if latest_block < from_block:
                    continue

                sig = PAIR_CREATED_SIG if dex["type"] == "v2" else POOL_CREATED_SIG
                logs = web3.eth.get_logs({
                    "fromBlock": from_block,
                    "toBlock": latest_block,
                    "address": dex["factory"],
                    "topics": [sig]
                })
                last_blocks[dex["name"]] = latest_block

                for log in logs:
                    # Extrai token0 e token1 dos tópicos (endereços em topics[1] e topics[2])
                    token0 = safe_checksum("0x" + log["topics"][1].hex()[-40:])
                    token1 = safe_checksum("0x" + log["topics"][2].hex()[-40:])

                    # Endereço do par/pool está no data do evento (últimos 20 bytes)
                    data_hex = log["data"].hex() if hasattr(log["data"], "hex") else str(log["data"])
                    pair_address = safe_checksum("0x" + data_hex[-40:])

                    logger.info(f"📦 [{dex['name']}] Par detectado: {pair_address} ({token0} / {token1})")

                    # Filtra por tokens-base (WETH/USDC) em pelo menos um lado
                    if not any(t in BASE_TOKENS for t in (token0, token1)):
                        logger.info("⏭ Ignorado: não contém token-base permitido.")
                        continue

                    notify(f"🆕 [{dex['name']}] Novo par: {pair_address}\nTokens: {token0} / {token1}", loop)

                    # Verificação de liquidez mínima:
                    # - Para V2: usa getReserves (ABI acima)
                    # - Para V3: pula essa checagem aqui; a estratégia validará depois
                    proceed = True
                    if dex["type"] == "v2":
                        proceed = has_min_liquidity_v2(web3, pair_address, safe_checksum(config["WETH"]), min_weth_wei)

                    if proceed:
                        if dex["type"] == "v2":
                            logger.info(f"💧 Liquidez mínima atingida em {dex['name']}.")
                        else:
                            logger.info(f"ℹ️ Pool V3 detectada — checagem de liquidez será feita na estratégia.")
                        sniper_pair_count += 1
                        last_pair_info = (pair_address, token0, token1)

                        # Passa dex_info para o callback (suporta sync e async)
                        try:
                            result = callback_on_pair(dex, pair_address, token0, token1)
                            if inspect.iscoroutine(result):
                                asyncio.run_coroutine_threadsafe(result, notify_loop or loop)
                        except Exception as cb_err:
                            logger.error(f"Erro no callback on_new_pair: {cb_err}", exc_info=True)
                            notify(f"⚠️ Erro no callback: {cb_err}", loop)
                    else:
                        logger.info("⏳ Ainda sem liquidez mínima.")
                        notify(f"⏳ Sem liquidez mínima no par {pair_address}.", loop)

        except Exception as e:
            logger.error(f"⚠️ Erro no loop de discovery: {e}", exc_info=True)
            notify(f"⚠️ Erro no loop de discovery: {e}", loop)

        time.sleep(interval)
