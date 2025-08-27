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

# sniper e discovery
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status
from config import config

# importa o singleton
from risk_manager import risk_manager

# log básico
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# Flask
app = Flask(__name__)

# globals
loop = asyncio.new_event_loop()
application = None
sniper_thread = None
_recent_pairs = set()  # mantém pares já notificados para evitar duplicatas

# callback de par com filtro de duplicatas
def _pair_callback(dex, pair, t0, t1):
    key = f"{dex}-{pair}"
    if key in _recent_pairs:
        return
    _recent_pairs.add(key)

    def _schedule_on_new_pair():
        task = loop.create_task(
            on_new_pair(dex, pair, t0, t1, bot=application.bot, loop=loop)
        )
        def _on_error(fut: asyncio.Future):
            if fut.cancelled():
                return
            exc = fut.exception()
            if exc:
                logging.error("❌ Erro em on_new_pair", exc_info=True)
        task.add_done_callback(_on_error)

    loop.call_soon_threadsafe(_schedule_on_new_pair)

# ambiente
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# auxiliares
def str_to_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY não definida.")
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
        f"💵 Trade: {os.getenv('TRADE_SIZE_ETH')} ETH\n"
        f"📉 Slippage: {os.getenv('SLIPPAGE_BPS')} bps\n"
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')}%\n"
        f"💧 Min. Liquidez WETH: {os.getenv('MIN_LIQ_WETH')}\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')}s\n"
        f"🧪 Dry Run: {os.getenv('DRY_RUN')}"
    )

# sniper thread
def iniciar_sniper():
    global sniper_thread, _recent_pairs
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ Sniper já rodando.")
        return

    logging.info("⚙️ Iniciando sniper...")
    _recent_pairs.clear()

    def _run():
        try:
            asyncio.run_coroutine_threadsafe(
                run_discovery(_pair_callback, loop),
                loop
            )
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=_run, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery(loop)

# handlers Telegram
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🎯 *Sniper Bot*\n\n"
        "Comandos:\n"
        "• /snipe — Iniciar sniper\n"
        "• /stop — Parar sniper\n"
        "• /sniperstatus — Status do sniper\n"
        "• /status — Saldo ETH/WETH\n"
        "• /ping — Pong\n"
        "• /testnotify — Teste de notificação\n"
        "• /menu — Menu\n"
        "• /relatorio — Relatório de eventos\n"
        "━━━━━━━━━━━━━━\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_text(texto, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        addr = context.args[0] if context.args else None
        sta = get_wallet_status(addr)
        await update.message.reply_text(sta)
    except Exception as e:
        logging.error(f"/status erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar carteira.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ Sniper já rodando.")
        return
    await update.message.reply_text("⚙️ Iniciando sniper...")
    iniciar_sniper()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        st = get_discovery_status() or {"text": "Indisponível"}
        await update.message.reply_text(st["text"])
    except Exception as e:
        logging.error(f"/sniperstatus erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro no status.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - context.bot_data.get("start_time", time.time()))
    up_str = str(datetime.timedelta(seconds=uptime))
    agora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"pong 🏓\nUptime: {up_str}\nAgora: {agora}")

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = int(TELEGRAM_CHAT_ID or 0)
    if chat == 0:
        await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID inválido.")
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = str(uuid.uuid4())[:8]
    await context.bot.send_message(chat_id=chat, text=f"✅ Teste {ts} ID:{uid}")
    await update.message.reply_text(f"Enviado ID:{uid}")

async def relatorio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        verbose = bool(context.args)
        report = risk_manager.generate_report(verbose)
        await update.message.reply_text(report)
    except Exception as e:
        logging.error(f"/relatorio erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao gerar relatório.")

# Healthcheck
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

# HTTP /relatorio
@app.route("/relatorio", methods=["GET"])
def relatorio_http():
    try:
        report = risk_manager.generate_report()
        return f"<h1>📊 Relatório</h1><pre>{report}</pre>"
    except Exception as e:
        logging.error(f"HTTP relatório erro: {e}", exc_info=True)
        return "Erro", 500

# Webhook
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if application is None:
            return "not ready", 503
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            loop
        )
        return "ok", 200
    except Exception as e:
        app.logger.error(f"Webhook erro: {e}", exc_info=True)
        return "error", 500

# registra webhook Telegram com retry
def set_webhook_with_retry(attempts=5, delay=3):
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logging.error("WEBHOOK não configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for i in range(attempts):
        try:
            resp = requests.post(url, json={"url": WEBHOOK_URL}, timeout=10)
            if resp.ok and resp.json().get("ok"):
                logging.info(f"Webhook registrado: {WEBHOOK_URL}")
                return
        except Exception as e:
            logging.warning(f"Webhook tentativa {i+1} falhou: {e}")
        time.sleep(delay)
    logging.error("Webhook todas tentativas falharam.")

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# boot
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN.")
        raise SystemExit(1)
    missing = [k for k in ("RPC_URL", "PRIVATE_KEY", "CHAIN_ID") if not os.getenv(k)]
    if missing:
        logging.error(f"Faltam variáveis: {missing}")
        raise SystemExit(1)

    try:
        addr = get_active_address()
        logging.info(f"Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Chave inválida: {e}", exc_info=True)
        raise SystemExit(1)

    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # registra handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("testnotify", test_notify_cmd))
    application.add_handler(CommandHandler("relatorio", relatorio_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    async def start_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand("start", "Boas-vindas"),
            BotCommand("menu", "Menu"),
            BotCommand("status", "Saldo"),
            BotCommand("snipe", "Iniciar sniper"),
            BotCommand("stop", "Parar sniper"),
            BotCommand("sniperstatus", "Status sniper"),
            BotCommand("ping", "Pong"),
            BotCommand("testnotify", "Teste notify"),
            BotCommand("relatorio", "Relatório eventos")
        ])

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e Flask iniciados")
    loop.run_forever()
