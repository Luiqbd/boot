# main.py

import os
import sys
import signal
import logging
import asyncio
import time
import datetime
import uuid
import argparse
from threading import Thread
from functools import wraps

from flask import Flask, request, jsonify, abort
from telegram import (
    Update, BotCommand,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)
from web3 import Web3

from config import config
from utils import escape_md_v2
from discovery import subscribe_new_pairs, stop_discovery, is_discovery_running
from pipeline import on_pair
from exit_manager import check_exits
from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status
from risk_manager import risk_manager
from metrics import init_metrics_server

# Métricas Prometheus
init_metrics_server(8000)

RPC_URL     = config["RPC_URL"]
TELE_TOKEN  = config["TELEGRAM_TOKEN"]
WEBHOOK_URL = config.get("WEBHOOK_URL", "")
PORT        = int(os.getenv("PORT", 10000))

# Logger
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    logger.error("RPC inacessível")
    sys.exit(1)

# Telegram Bot
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

app_bot = ApplicationBuilder().token(TELE_TOKEN).build()
bot = app_bot.bot
app_bot.bot_data["start_time"] = time.time()

def build_menu():
    kb = [
        [InlineKeyboardButton("▶ Iniciar Sniper", "menu_snipe"),
         InlineKeyboardButton("⏹ Parar Sniper",   "menu_stop")],
        [InlineKeyboardButton("📊 Status",       "menu_status"),
         InlineKeyboardButton("💰 Saldo",        "menu_balance")],
        [InlineKeyboardButton("🏓 Ping",         "menu_ping"),
         InlineKeyboardButton("🔔 TesteNotif",   "menu_testnotify")],
        [InlineKeyboardButton("📑 Relatório",    "menu_report")]
    ]
    return InlineKeyboardMarkup(kb)

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_markdown_v2(
        "🎯 *Sniper Bot*\nUse os botões abaixo:",
        reply_markup=build_menu()
    )

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cmd = q.data
    if cmd == "menu_snipe":
        token = gerar_meu_token_externo()
        if not token:
            await q.message.reply_text("❌ Auth0 falhou")
        else:
            subscribe_new_pairs(on_pair, loop)
            await q.message.reply_text("🟢 Sniper iniciado")

    elif cmd == "menu_stop":
        stop_discovery()
        await q.message.reply_text("🔴 Sniper parado")

    elif cmd == "menu_status":
        status = "🟢 Ativo" if is_discovery_running() else "🔴 Parado"
        await q.message.reply_text(f"*Status:* {status}", parse_mode="MarkdownV2")

    elif cmd == "menu_balance":
        await q.message.reply_text(get_wallet_status())

    elif cmd == "menu_ping":
        up = int(time.time() - app_bot.bot_data["start_time"])
        await q.message.reply_text(f"pong 🏓\nUptime: {datetime.timedelta(seconds=up)}")

    elif cmd == "menu_testnotify":
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = uuid.uuid4().hex[:6]
        await bot.send_message(chat_id=config["TELEGRAM_CHAT_ID"], text=f"✅ Teste {ts}\nID:{uid}")
        await q.message.reply_text(f"🔔 Enviado (ID={uid})")

    elif cmd == "menu_report":
        await q.message.reply_text(risk_manager.gerar_relatorio())

    # reexibe menu
    try:
        await q.message.edit_markdown_v2(
            "🎯 *Sniper Bot*\nUse os botões abaixo:",
            reply_markup=build_menu()
        )
    except:
        await q.message.reply_markdown_v2(
            "🎯 *Sniper Bot*\nUse os botões abaixo:",
            reply_markup=build_menu()
        )

# Registrar handlers
app_bot.add_handler(CommandHandler("start", start_cmd))
app_bot.add_handler(CallbackQueryHandler(menu_handler))
app_bot.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND,
                   lambda u,c: u.message.reply_text("Use /start"))
)

# Comandos
loop.run_until_complete(app_bot.initialize())
loop.run_until_complete(app_bot.start())
loop.run_until_complete(bot.set_my_commands([BotCommand("start","Abrir menu")]))
if WEBHOOK_URL:
    url = WEBHOOK_URL.rstrip("/") + "/webhook"
    loop.run_until_complete(bot.set_webhook(url=url))

Thread(target=loop.run_forever, daemon=True).start()
logger.info("🤖 Bot running")

# Flask API
api = Flask(__name__)

@api.route("/api/token")
def api_token():
    tok = gerar_meu_token_externo()
    return jsonify({"token":tok}) if tok else ("{}",502)

@api.route("/api/status")
def api_status():
    return jsonify({"active": is_discovery_running()})

@api.route("/webhook", methods=["POST"])
def api_webhook():
    data = request.get_json(silent=True)
    if not data or not ("message" in data or "callback_query" in data):
        return "ignored",200
    upd = Update.de_json(data, bot)
    loop.call_soon_threadsafe(asyncio.create_task, app_bot.process_update(upd))
    return "ok",200

# Graceful shutdown
def shutdown(sig, frame):
    stop_discovery()
    fut = asyncio.run_coroutine_threadsafe(app_bot.shutdown(), loop)
    try: fut.result(10)
    except: pass
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, shutdown)

# Entry point
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()

    if args.worker:
        subscribe_new_pairs(on_pair, loop)
        while True:
            asyncio.get_event_loop().run_until_complete(check_exits())
    else:
        api.run("0.0.0.0", PORT, threaded=True)
