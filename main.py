import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from threading import Thread
import time
import datetime
import uuid
from web3 import Web3

# --- Importações de sniper e executor ---
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status
from trade_executor import RealTradeExecutor, SafeTradeExecutor
from config import config

# --- Configuração de log ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Flask app ---
app = Flask(__name__)

# --- Loop e variáveis globais ---
loop = asyncio.new_event_loop()
application = None
sniper_thread = None
executor = None  # Será inicializado no __main__

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# --- Helpers ---
def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY não definida no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("PRIVATE_KEY inválida.")
    return pk

def get_active_address() -> str:
    raw = os.getenv("PRIVATE_KEY")
    pk = normalize_private_key(raw)
    return Web3().eth.account.from_key(pk).address

def env_summary_text() -> str:
    try:
        addr = get_active_address()
    except Exception as e:
        addr = f"Erro: {e}"
    return (
        f"🔑 Endereço: `{addr}`\n"
        f"🌐 Chain ID: {os.getenv('CHAIN_ID')}\n"
        f"🔗 RPC: {os.getenv('RPC_URL')}\n"
        f"💵 Trade size: {os.getenv('TRADE_SIZE_ETH')} ETH\n"
        f"📉 Slippage: {os.getenv('SLIPPAGE_BPS')} bps\n"
        f"🛑 Stop Loss: {os.getenv('STOP_LOSS_PCT')} %\n"
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')} %\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')} s\n"
    )

# --- Funções de controle do sniper ---
def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logger.info("⚠️ Sniper já está rodando.")
        return

    logger.info("⚙️ Iniciando sniper em todas as DEX...")
    def runner():
        try:
            run_discovery(
                lambda dex, pair, t0, t1: on_new_pair(
                    dex, pair, t0, t1,
                    bot=application.bot,
                    loop=loop,
                    executor=executor
                ),
                loop
            )
        except Exception as e:
            logger.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=runner, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery(loop)

# --- Handlers de Telegram ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🎯 *Bem-vindo ao Sniper Bot*\n\n"
        "📌 *Comandos disponíveis*\n"
        "/snipe — Inicia o sniper\n"
        "/stop — Para o sniper\n"
        "/sniper_status — Status do sniper\n"
        "/status — Saldo ETH/WETH\n"
        "/ping — Teste de vida\n"
        "/test_notify — Notificação de teste\n"
        "/menu — Reexibe este menu\n\n"
        "*Configuração atual:*\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        addr = context.args[0] if context.args else None
        status = get_wallet_status(addr)
        await update.message.reply_text(status)
    except Exception as e:
        logger.error(f"Erro em /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar saldo.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ Sniper já está rodando.")
        return
    await update.message.reply_text("🚀 Iniciando sniper...")
    iniciar_sniper()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        st = get_discovery_status() or {"text": "Indisponível"}
        await update.message.reply_text(st["text"])
    except Exception as e:
        logger.error(f"Erro em /sniper_status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar status.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - context.bot_data.get("start_time", time.time()))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"pong 🏓\nUptime: {uptime}s\nAgora: {now}")

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cid = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0
        if cid == 0:
            await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID inválido.")
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = str(uuid.uuid4())[:8]
        await context.bot.send_message(
            chat_id=cid,
            text=f"✅ Teste de notificação\n🕒 {ts}\n🔢 {uid}"
        )
        await update.message.reply_text(f"Mensagem enviada (ID {uid})")
    except Exception as e:
        logger.error(f"Erro em /test_notify: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Falha ao enviar notificação.")

# --- Endpoints Flask ---
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if application is None:
        return "not ready", 503
    data = request.get_json(force=True)
    upd  = Update.de_json(data, application.bot)
    asyncio.run_coroutine_threadsafe(application.process_update(upd), loop)
    return "ok", 200

def set_webhook_with_retry(attempts=5, delay=3):
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logger.error("Falta TELEGRAM_TOKEN ou WEBHOOK_URL.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for i in range(attempts):
        try:
            r = requests.post(url, json={"url": WEBHOOK_URL}, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                logger.info("✅ Webhook registrado.")
                return
            logger.warning(f"Tentativa {i+1} falhou: {r.text}")
        except Exception as e:
            logger.warning(f"Tentativa {i+1} erro: {e}")
        time.sleep(delay)
    logger.error("❌ Falha ao registrar webhook.")

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# --- Inicialização principal ---
if __name__ == "__main__":
    # 1) validações essenciais
    if not TELEGRAM_TOKEN:
        logger.error("Falta TELEGRAM_TOKEN. Encerrando.")
        raise SystemExit(1)
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL não definido; sem webhook.")
    missing = [k for k in ["RPC_URL", "PRIVATE_KEY", "CHAIN_ID"] if not os.getenv(k)]
    if missing:
        logger.error(f"Faltam variáveis: {missing}. Encerrando.")
        raise SystemExit(1)

    # 2) valida privada e log
    try:
        addr = get_active_address()
        logger.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logger.error(f"Chave inválida: {e}", exc_info=True)
        raise SystemExit(1)

    # 3) instancia executor (real vs simulado)
    dry_run = str_to_bool(os.getenv("DRY_RUN", "true"))
    logger.info(f"🔄 Modo dry_run: {dry_run}")

    w3             = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
    wallet_address = get_active_address()
    trade_size     = float(os.getenv("TRADE_SIZE_ETH", "0.01"))
    slippage_bps   = int(os.getenv("SLIPPAGE_BPS", "50"))

    executor = (
        SafeTradeExecutor(w3, wallet_address, trade_size, slippage_bps, dry_run=dry_run)
        if dry_run
        else RealTradeExecutor(w3, wallet_address, trade_size, slippage_bps, dry_run=dry_run)
    )

    # 4) configura bot Telegram + Flask
    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniper_status", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("test_notify", test_notify_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    async def start_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand("start", "Boas-vindas e config"),
            BotCommand("menu", "Menu"),
            BotCommand("status", "Saldo ETH/WETH"),
            BotCommand("snipe", "Inicia sniper"),
            BotCommand("stop", "Para sniper"),
            BotCommand("sniper_status", "Status sniper"),
            BotCommand("ping", "pong"),
            BotCommand("test_notify", "Teste notificação")
        ])

        # log de DEXes monitoradas
        try:
            dexes = config.get("DEXES", [])
            if dexes:
                lines = [f"- {d['name']} ({d['type']})" for d in dexes]
                logger.info("🔎 DEX monitoradas:\n" + "\n".join(lines))
        except Exception as e:
            logger.warning(f"Falha ao listar DEXES: {e}")

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logger.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
