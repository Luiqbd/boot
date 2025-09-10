# main.py

import os
import sys
import signal
import logging
import asyncio
import time
import datetime
import uuid
from functools import wraps
from threading import Thread
from decimal import Decimal

from flask import Flask, request, jsonify, abort
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from web3 import Web3

from config import config
from utils import escape_md_v2
from discovery import subscribe_new_pairs, stop_discovery, is_discovery_running
from exchange_client import ExchangeClient
from risk_manager import risk_manager
from strategy_sniper import on_new_pair
from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status

# ─── Configurações básicas ──────────────────────────────────────────────
RPC_URL    = config["RPC_URL"]
CHAIN_ID   = int(config["CHAIN_ID"])
TELE_TOKEN = config["TELEGRAM_TOKEN"]
TELE_CHAT  = config["TELEGRAM_CHAT_ID"]
PORT       = int(os.getenv("PORT", 10000))

# ─── Logger ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Conexão Web3 e ExchangeClient ───────────────────────────────────────
web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not web3.isConnected():
    logger.error("Falha ao conectar no RPC %s", RPC_URL)
    sys.exit(1)

# Usamos o primeiro router da lista, mas você pode trocar conforme precisar
exchange_client = ExchangeClient(config["DEXES"][0].router)

# ─── Telegram Bot Setup ──────────────────────────────────────────────────
telegram_loop = asyncio.new_event_loop()
asyncio.set_event_loop(telegram_loop)

application = ApplicationBuilder().token(TELE_TOKEN).build()
app_bot = application.bot
application.bot_data["start_time"] = time.time()

# Comandos Telegram
async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🎯 *Sniper Bot*\n\n"
        "/snipe — iniciar sniper\n"
        "/stop — parar sniper\n"
        "/sniperstatus — status sniper\n"
        "/status — saldo ETH/WETH\n"
        "/ping — alive check\n"
        "/testnotify — notificação teste\n"
        "/menu — este menu\n"
        "/relatorio — relatório de eventos\n\n"
        "*Config atual:*\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_markdown_v2(texto)

async def snipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Iniciando sniper...", parse_mode="MarkdownV2")
    iniciar_sniper()

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.", parse_mode="MarkdownV2")

async def sniper_status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = "🟢 Ativo" if is_discovery_running() else "🔴 Parado"
    await update.message.reply_text(msg)

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addr = ctx.args[0] if ctx.args else None
    bal = get_wallet_status(addr)
    await update.message.reply_text(bal)

async def ping_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    up = int(time.time() - ctx.bot_data["start_time"])
    await update.message.reply_text(f"pong 🏓\n⏱ Uptime: {datetime.timedelta(seconds=up)}")

async def testnotify_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = uuid.uuid4().hex[:6]
    text = f"✅ Teste 🕒{ts}\nID: `{uid}`"
    await app_bot.send_message(chat_id=TELE_CHAT, text=text, parse_mode="MarkdownV2")
    await update.message.reply_text(f"Enviado (ID={uid})")

async def relatorio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    report = risk_manager.gerar_relatorio()
    await update.message.reply_text(report)

async def echo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = escape_md_v2(update.message.text)
    await update.message.reply_text(f"Você disse: {txt}")

# Registra handlers
cmds = [
    ("start", start_cmd),
    ("menu", start_cmd),
    ("snipe", snipe_cmd),
    ("stop", stop_cmd),
    ("sniperstatus", sniper_status_cmd),
    ("status", status_cmd),
    ("ping", ping_cmd),
    ("testnotify", testnotify_cmd),
    ("relatorio", relatorio_cmd),
]
for name, handler in cmds:
    application.add_handler(CommandHandler(name, handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

# Define comandos visíveis
telegram_loop.run_until_complete(
    app_bot.set_my_commands([BotCommand(n, h.__doc__ or "") for n, h in cmds])
)

# Roda o bot Telegram em thread separada
Thread(target=telegram_loop.run_forever, daemon=True).start()
logger.info("🛰️ Telegram bot rodando em background")


# ─── Discovery / Sniper Orquestração ────────────────────────────────────
def iniciar_sniper():
    if is_discovery_running():
        logger.info("⚠️ Sniper já ativo")
        return

    token = fetch_token()
    if not token:
        logger.error("❌ Sem token Auth0, abortando sniper")
        return

    def _cb(pair_address, token0, token1, dex_info):
        coro = on_new_pair(
            dex_info, pair_address, token0, token1,
            bot=app_bot, loop=telegram_loop, token=token
        )
        asyncio.run_coroutine_threadsafe(coro, telegram_loop)

    subscribe_new_pairs(callback=_cb)
    logger.info("🟢 Sniper iniciado")

def parar_sniper():
    stop_discovery()
    logger.info("🔴 Sniper parado")

def env_summary_text() -> str:
    addr = web3.eth.account.from_key(config["PRIVATE_KEY"]).address
    return (
        f"🔑 `{addr}`\n"
        f"🌐 Chain ID: {CHAIN_ID}\n"
        f"🔗 RPC: {RPC_URL}\n"
        f"⏱ Disc Interval: {config['DISCOVERY_INTERVAL']}s\n"
        f"🧪 Dry Run: {config['DRY_RUN']}"
    )

def fetch_token() -> str:
    try:
        t = gerar_meu_token_externo()
        logger.info("✅ Token Auth0 obtido")
        return t
    except Exception as e:
        logger.error("❌ Erro Auth0: %s", e, exc_info=True)
        return ""


# ─── Flask API ───────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/api/token", methods=["GET"])
def api_token():
    t = fetch_token()
    if not t:
        return jsonify({"error": "Auth0 fail"}), 502
    return jsonify({"token": t})

def require_auth(f):
    @wraps(f)
    def inner(*args, **kwargs):
        hdr = request.headers.get("Authorization", "")
        if not hdr.lower().startswith("bearer "):
            abort(401)
        # TODO: validar JWT contra Auth0
        return f(*args, **kwargs)
    return inner

@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    return jsonify({"sniper_active": is_discovery_running()})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return "ignored", 200

    upd = Update.de_json(data, app_bot)
    asyncio.run_coroutine_threadsafe(application.process_update(upd), telegram_loop)
    return "ok", 200


# ─── Shutdown Graceful ───────────────────────────────────────────────────
def _shutdown(signum, frame):
    logger.info("Recebido signal %s, encerrando...", signum)
    parar_sniper()
    asyncio.run(application.shutdown())
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, _shutdown)


# ─── Entry Point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Verifica chave antes de tudo
    try:
        _ = web3.eth.account.from_key(config["PRIVATE_KEY"]).address
    except Exception as e:
        logger.error("PRIVATE_KEY inválida: %s", e)
        sys.exit(1)

    # Inicia Flask (bot já rodando em background)
    logger.info("🚀 Iniciando Flask API na porta %s", PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
