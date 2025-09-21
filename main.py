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
from functools import wraps
from threading import Thread

from flask import Flask, request, jsonify, abort
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
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

# ─── Inicia servidor de métricas Prometheus ─────────────────────────
init_metrics_server(port=8000)

# ─── Config Básicas ─────────────────────────────────────────────────
RPC_URL    = config["RPC_URL"]
CHAIN_ID   = int(config["CHAIN_ID"])
TELE_TOKEN = config["TELEGRAM_TOKEN"]
WEBHOOK    = config.get("WEBHOOK_URL", "")
PORT       = int(os.getenv("PORT", 10000))

# ─── Logger ─────────────────────────────────────────────────────────
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Conexão Web3 & Validações ───────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    logger.error("Falha ao conectar no RPC %s", RPC_URL)
    sys.exit(1)

if not config.get("DEXES"):
    logger.error("Nenhuma DEX configurada (DEX_1_*).")
    sys.exit(1)

# ─── Setup do Bot Telegram ────────────────────────────────────────────
telegram_loop = asyncio.new_event_loop()
asyncio.set_event_loop(telegram_loop)

application = ApplicationBuilder().token(TELE_TOKEN).build()
bot = application.bot
application.bot_data["start_time"] = time.time()


def fetch_token() -> str:
    try:
        token = gerar_meu_token_externo()
        logger.info("✅ Token Auth0 obtido")
        return token
    except Exception as e:
        logger.error("❌ Falha ao obter token Auth0: %s", e, exc_info=True)
        return ""


def env_summary_text() -> str:
    addr = w3.eth.account.from_key(config["PRIVATE_KEY"]).address
    return (
        f"🔑 Endereço: {addr}\n"
        f"🌐 Chain ID: {CHAIN_ID}\n"
        f"🔗 RPC: {RPC_URL}\n"
        f"⏱ Discovery Interval: {config['DISCOVERY_INTERVAL']}s\n"
        f"🧪 Dry Run: {config['DRY_RUN']}"
    )


def build_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("▶️ Iniciar Sniper", callback_data="menu_snipe"),
            InlineKeyboardButton("⏹️ Parar Sniper",   callback_data="menu_stop"),
        ],
        [
            InlineKeyboardButton("📊 Status Sniper",  callback_data="menu_status"),
            InlineKeyboardButton("💰 Saldo ETH/WETH", callback_data="menu_balance"),
        ],
        [
            InlineKeyboardButton("🏓 Ping",           callback_data="menu_ping"),
            InlineKeyboardButton("🛎️ Teste Notif.",  callback_data="menu_testnotify"),
        ],
        [
            InlineKeyboardButton("📑 Relatório Risco", callback_data="menu_report"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Exibe menu principal com botões."""
    text = (
        "🎯 *Sniper Bot*\n\n"
        "Use os botões abaixo para controlar o bot:\n"
    )
    await update.message.reply_markdown_v2(
        text, 
        reply_markup=build_main_menu()
    )


async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle button presses from the main menu."""
    query = update.callback_query
    await query.answer()
    cmd = query.data

    if cmd == "menu_snipe":
        await query.message.reply_text("⚙️ Iniciando sniper...")
        token = fetch_token()
        if not token:
            await query.message.reply_text("❌ Falha ao obter token Auth0")
        else:
            iniciar_sniper()
            await query.message.reply_text("🟢 Sniper iniciado")

    elif cmd == "menu_stop":
        parar_sniper()
        await query.message.reply_text("🛑 Sniper interrompido")

    elif cmd == "menu_status":
        status = "🟢 Ativo" if is_discovery_running() else "🔴 Parado"
        await query.message.reply_text(f"📊 Status Sniper: *{status}*", parse_mode="MarkdownV2")

    elif cmd == "menu_balance":
        bal_text = get_wallet_status(None)
        await query.message.reply_text(f"💰 {bal_text}")

    elif cmd == "menu_ping":
        up = int(time.time() - ctx.bot_data["start_time"])
        await query.message.reply_text(f"pong 🏓\nUptime: {datetime.timedelta(seconds=up)}")

    elif cmd == "menu_testnotify":
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = uuid.uuid4().hex[:6]
        texto = f"✅ Teste {ts}\nID: {uid}"
        await bot.send_message(chat_id=config["TELEGRAM_CHAT_ID"], text=texto)
        await query.message.reply_text(f"🛎️ Notificação enviada (ID={uid})")

    elif cmd == "menu_report":
        report = risk_manager.gerar_relatorio()
        await query.message.reply_text(f"📑 Relatório de risco:\n{report}")

    # re-exibe o menu
    await query.message.reply_text(
        "🎯 Menu Principal:",
        reply_markup=build_main_menu()
    )


# registra handlers
application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(CallbackQueryHandler(menu_handler))

# fallback echo
application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, 
                   lambda u, c: u.message.reply_text("Use /start para abrir o menu"))
)

# registra comandos no Telegram para UX
commands = [
    BotCommand("start", "Abrir menu principal do Sniper Bot"),
]
telegram_loop.run_until_complete(application.initialize())
telegram_loop.run_until_complete(application.start())
telegram_loop.run_until_complete(bot.set_my_commands(commands))

if WEBHOOK:
    telegram_loop.run_until_complete(bot.set_webhook(url=WEBHOOK))
    logger.info("✅ Webhook configurado em %s", WEBHOOK)

Thread(target=telegram_loop.run_forever, daemon=True).start()
logger.info("🚀 Bot Telegram rodando em background")


# ─── Controle do Sniper ───────────────────────────────────────────────
def iniciar_sniper():
    if is_discovery_running():
        logger.info("⚠️ Sniper já está ativo")
        return

    def _cb(pair_addr, token0, token1, dex_info):
        asyncio.run_coroutine_threadsafe(
            on_pair(pair_addr, token0, token1, dex_info),
            telegram_loop
        )

    subscribe_new_pairs(callback=_cb)
    logger.info("🟢 SniperDiscovery iniciado")


def parar_sniper():
    stop_discovery()
    logger.info("🔴 SniperDiscovery parado")


# ─── Flask API ─────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/api/token", methods=["GET"])
def api_token():
    token = fetch_token()
    if not token:
        return jsonify({"error": "Auth0 falhou"}), 502
    return jsonify({"token": token})

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        hdr = request.headers.get("Authorization", "")
        if not hdr.lower().startswith("bearer "):
            abort(401)
        return f(*args, **kwargs)
    return wrapper

@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    return jsonify({"sniper_active": is_discovery_running()})

@app.route("/webhook", methods=["POST"])
def api_webhook():
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return "ignored", 200
    upd = Update.de_json(data, bot)
    asyncio.run_coroutine_threadsafe(application.process_update(upd), telegram_loop)
    return "ok", 200


# ─── Shutdown gracioso ─────────────────────────────────────────────────
def _shutdown(sig, frame):
    parar_sniper()
    future = asyncio.run_coroutine_threadsafe(application.shutdown(), telegram_loop)
    try:
        future.result(timeout=10)
    except Exception as e:
        logger.error("Erro ao parar Telegram Application: %s", e, exc_info=True)
    logger.info("🔴 Telegram Application parado")
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, _shutdown)


# ─── Entry Point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--worker", action="store_true",
        help="Executar modo worker (descoberta + trading + exit)"
    )
    args = parser.parse_args()

    try:
        _ = w3.eth.account.from_key(config["PRIVATE_KEY"]).address
    except Exception as e:
        logger.error("PRIVATE_KEY inválida: %s", e)
        sys.exit(1)

    if args.worker:
        logger.info("▶️ Iniciando Worker Mode")
        subscribe_new_pairs(callback=on_pair)
        while True:
            try:
                coro = check_exits()
                if asyncio.iscoroutine(coro):
                    asyncio.get_event_loop().run_until_complete(coro)
            except Exception:
                logger.exception("Erro no gerenciador de saídas")
            time.sleep(config["EXIT_POLL_INTERVAL"])
    else:
        logger.info("🚀 Iniciando API Flask na porta %s", PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True)
