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
from discovery import (
    subscribe_new_pairs,
    stop_discovery,
    is_discovery_running
)
from pipeline import on_pair
from exit_manager import check_exits
from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status
from risk_manager import risk_manager
from metrics import init_metrics_server

# ─── Inicia servidor de métricas Prometheus ─────────────────────────
init_metrics_server(port=8000)

# ─── Configurações básicas ───────────────────────────────────────────
RPC_URL    = config["RPC_URL"]
CHAIN_ID   = int(config["CHAIN_ID"])
TELE_TOKEN = config["TELEGRAM_TOKEN"]
TELE_CHAT  = config["TELEGRAM_CHAT_ID"]
WEBHOOK    = config.get("WEBHOOK_URL", "")
PORT       = int(os.getenv("PORT", 10000))

# ─── Logger ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Conexão Web3 & validações ───────────────────────────────────────
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
    """
    Obtém token Auth0 para autorizar chamadas à API.
    """
    try:
        token = gerar_meu_token_externo()
        logger.info("✅ Token Auth0 obtido")
        return token
    except Exception as e:
        logger.error("❌ Falha ao obter token Auth0: %s", e, exc_info=True)
        return ""

def log_cmd(cmd: str, update: Update):
    user = update.effective_user.username or update.effective_user.id
    logger.info("🛎 Comando /%s por %s", cmd, user)

def env_summary_text() -> str:
    addr = w3.eth.account.from_key(config["PRIVATE_KEY"]).address
    return (
        f"🔑 {addr}\n"
        f"🌐 Chain ID: {CHAIN_ID}\n"
        f"🔗 RPC: {RPC_URL}\n"
        f"⏱ Discovery Interval: {config['DISCOVERY_INTERVAL']}s\n"
        f"🧪 Dry Run: {config['DRY_RUN']}"
    )

# ─── Handlers Telegram ───────────────────────────────────────────────

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Exibe o menu principal do Sniper Bot."""
    log_cmd("start", update)
    texto = (
        "🎯 Sniper Bot\n\n"
        "/snipe — iniciar descoberta e trading\n"
        "/stop — parar sniper\n"
        "/sniperstatus — status do sniper\n"
        "/status — saldo ETH/WETH\n"
        "/ping — checar alive\n"
        "/testnotify — notificação teste\n"
        "/relatorio — relatório de risco\n\n"
        "Config atual:\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_text(texto)

async def snipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inicia a descoberta de pares e execução de trades."""
    log_cmd("snipe", update)
    await update.message.reply_text("⚙️ Iniciando sniper (modo API)...")
    token = fetch_token()
    if not token:
        await update.message.reply_text("❌ Falha ao obter token Auth0")
        return
    iniciar_sniper()
    await update.message.reply_text("🟢 Sniper iniciado")

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Para o Sniper Bot."""
    log_cmd("stop", update)
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido")

async def sniper_status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Exibe status do Sniper (ativo/parado)."""
    log_cmd("sniperstatus", update)
    status = "🟢 Ativo" if is_discovery_running() else "🔴 Parado"
    await update.message.reply_text(status)

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Consulta saldo ETH/WETH da carteira."""
    log_cmd("status", update)
    addr = ctx.args[0] if ctx.args else None
    bal_text = get_wallet_status(addr)
    await update.message.reply_text(bal_text)

async def ping_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Checa se o Bot está vivo e mostra uptime."""
    log_cmd("ping", update)
    up = int(time.time() - ctx.bot_data["start_time"])
    await update.message.reply_text(f"pong 🏓\nUptime: {datetime.timedelta(seconds=up)}")

async def testnotify_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Envia notificação de teste no Telegram."""
    log_cmd("testnotify", update)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = uuid.uuid4().hex[:6]
    texto = f"✅ Teste {ts}\nID: {uid}"
    await bot.send_message(chat_id=TELE_CHAT, text=texto)
    await update.message.reply_text(f"Enviado (ID={uid})")

async def relatorio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Gera relatório de risco e PnL."""
    log_cmd("relatorio", update)
    report = risk_manager.gerar_relatorio()
    await update.message.reply_text(report)

async def echo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ecoa mensagens de texto simples."""
    txt = escape_md_v2(update.message.text)
    await update.message.reply_text(f"Você disse: {txt}")

# Lista de handlers e descrições de comando
handlers = [
    ("start",        start_cmd),
    ("menu",         start_cmd),
    ("snipe",        snipe_cmd),
    ("stop",         stop_cmd),
    ("sniperstatus", sniper_status_cmd),
    ("status",       status_cmd),
    ("ping",         ping_cmd),
    ("testnotify",   testnotify_cmd),
    ("relatorio",    relatorio_cmd),
]

command_descriptions = [
    ("start",        "Exibe o menu principal do Sniper Bot"),
    ("menu",         "Exibe o menu principal"),
    ("snipe",        "Inicia descoberta e execução de trades"),
    ("stop",         "Para o Sniper Bot"),
    ("sniperstatus","Exibe status do Sniper (ativo/parado)"),
    ("status",       "Consulta saldo ETH/WETH"),
    ("ping",         "Checa alive e mostra uptime"),
    ("testnotify",   "Envia notificação de teste"),
    ("relatorio",    "Gera relatório de risco e PnL"),
]

# Adiciona os handlers
for name, fn in handlers:
    application.add_handler(CommandHandler(name, fn))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_cmd))

# Registra comandos no Telegram com descrições não vazias
cmds = [BotCommand(name, desc) for name, desc in command_descriptions]
telegram_loop.run_until_complete(application.initialize())
telegram_loop.run_until_complete(application.start())
telegram_loop.run_until_complete(bot.set_my_commands(cmds))

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
    # Para descoberta de pares
    parar_sniper()

    # Agendando shutdown do Application no telegram_loop
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
        "--worker",
        action="store_true",
        help="Executar modo worker (descoberta + trading + exit)"
    )
    args = parser.parse_args()

    # valida PRIVATE_KEY
    try:
        _ = w3.eth.account.from_key(config["PRIVATE_KEY"]).address
    except Exception as e:
        logger.error("PRIVATE_KEY inválida: %s", e)
        sys.exit(1)

    if args.worker:
        # Modo Worker: discovery → pipeline e loop de exits
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
        # Modo API + Telegram + Flask
        logger.info("🚀 Iniciando API Flask na porta %s", PORT)
        app.run(host="0.0.0.0", port=PORT, threaded=True)
