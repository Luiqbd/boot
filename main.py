# main.py

from dotenv import load_dotenv
load_dotenv()  # carrega .env

import os
import asyncio
import logging
import requests
import time
import datetime
import uuid
from threading import Thread
from decimal import Decimal
from functools import wraps

from flask import Flask, request, jsonify, abort
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from web3 import Web3

from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status, DexInfo
from risk_manager import RiskManager
from config import config

# --- Logger ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("main")

# --- Flask App ---
app = Flask(__name__)

# --- Globals & ENV ---
loop            = None
application     = None
sniper_thread   = None

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

RPC_URL     = os.getenv("RPC_URL", "").strip()
CHAIN_ID    = os.getenv("CHAIN_ID", "").strip()

dexes = [
    DexInfo(name=d.name, factory=d.factory, router=d.router, type=d.type)
    for d in config.get("DEXES", [])
]
base_tokens  = config.get("BASE_TOKENS", [config.get("WETH")])
MIN_LIQ_WETH = Decimal(str(config.get("MIN_LIQ_WETH", "0.5")))
INTERVAL_SEC = int(config.get("INTERVAL", 3))

risk_manager = RiskManager()


# --- Helpers ---
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        return f(*args, **kwargs)
    return decorated


def fetch_token() -> str | None:
    cid = os.getenv("CLIENT_ID", "").strip()
    cs  = os.getenv("CLIENT_SECRET", "").strip()
    if not cid or not cs:
        logger.error("CLIENT_ID/CLIENT_SECRET não configurados.")
        return None
    try:
        return gerar_meu_token_externo(cid, cs)
    except Exception as e:
        logger.error("Erro ao obter token: %s", e, exc_info=True)
        return None


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
    pk = os.getenv("PRIVATE_KEY", "")
    return Web3().eth.account.from_key(normalize_private_key(pk)).address


def validate_sniper_config():
    missing = [v for v in ("RPC_URL", "PRIVATE_KEY", "CHAIN_ID") if not os.getenv(v)]
    if missing:
        logger.error("Faltam variáveis obrigatórias do sniper: %s", missing)
        raise SystemExit(1)


def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logger.info("⚠️ Sniper já está rodando.")
        return

    logger.info("⚙️ Iniciando sniper...")
    def _runner():
        token = fetch_token()
        if not token:
            logger.error("❌ Token não obtido, abortando sniper.")
            return

        try:
            run_discovery(
                Web3(Web3.HTTPProvider(RPC_URL)),
                dexes,
                base_tokens,
                MIN_LIQ_WETH,
                INTERVAL_SEC,
                application.bot,
                lambda pair: on_new_pair(
                    pair.dex,
                    pair.address,
                    pair.token0,
                    pair.token1,
                    bot=application.bot,
                    loop=loop,
                    token=token
                )
            )
        except Exception as e:
            logger.error("Erro em discovery: %s", e, exc_info=True)

    sniper_thread = Thread(target=_runner, daemon=True)
    sniper_thread.start()


def parar_sniper():
    stop_discovery()
    logger.info("🛑 Sniper parado.")


def env_summary_text() -> str:
    try:
        addr = get_active_address()
    except Exception as e:
        addr = f"Erro: {e}"
    return (
        f"🔑 Endereço: `{addr}`\n"
        f"🌐 Chain ID: {CHAIN_ID}\n"
        f"🔗 RPC: {RPC_URL}\n"
        f"💵 Trade: {os.getenv('TRADE_SIZE_ETH') or '—'} ETH\n"
        f"📉 Slippage: {os.getenv('SLIPPAGE_BPS') or '—'} bps\n"
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT') or '—'}%\n"
        f"⏱ Intervalo: {INTERVAL_SEC}s\n"
        f"🧪 Dry Run: {os.getenv('DRY_RUN') or 'false'}"
    )


# --- HTTP Endpoints ---
@app.route("/api/token", methods=["GET"])
def get_token():
    cid = os.getenv("CLIENT_ID", "").strip()
    cs  = os.getenv("CLIENT_SECRET", "").strip()
    if not cid or not cs:
        return jsonify({"error": "Credenciais não configuradas"}), 500
    try:
        token = gerar_meu_token_externo(cid, cs)
    except Exception as e:
        logger.error("Erro ao gerar token: %s", e, exc_info=True)
        return jsonify({"error": "Falha ao gerar token"}), 502
    return jsonify({"token": token}), 200


@app.route("/api/comprar", methods=["POST"])
@require_auth
def comprar():
    payload = request.get_json(silent=True) or {}
    par     = payload.get("par")
    return jsonify({"status": "comprando", "par": par}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    logger.info("INCOMING WEBHOOK: headers=%s body=%s",
                dict(request.headers), request.get_data())
    if application is None:
        return "not ready", 503

    data = request.get_json(silent=True)
    if not data:
        logger.warning("Webhook: payload vazio ou não-JSON")
        return "no data", 400

    if "message" in data:
        try:
            update = Update.de_json(data, application.bot)
            asyncio.run_coroutine_threadsafe(
                application.process_update(update), loop
            )
            return "ok", 200
        except Exception as e:
            logger.error("Erro no webhook: %s", e, exc_info=True)
            return "error", 500

    return "ignored", 200


# --- Telegram Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎯 **Bem-vindo ao Sniper Bot**\n\n"
        "• /snipe — inicia sniper\n"
        "• /stop — para sniper\n"
        "• /sniperstatus — status sniper\n"
        "• /status — saldo ETH/WETH\n"
        "• /ping — teste de vida\n"
        "• /testnotify — notificação teste\n"
        "• /menu — mostra este menu\n"
        "• /relatorio — relatório de eventos\n\n"
        "**Configuração Atual:**\n" +
        env_summary_text()
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        addr = context.args[0] if context.args else None
        bal  = get_wallet_status(addr)
        await update.message.reply_text(bal)
    except Exception as e:
        logger.error("Erro em /status: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Erro ao consultar saldo.")


async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Iniciando sniper via /snipe...")
    iniciar_sniper()


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")


async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ativo = get_discovery_status()
    msg   = "🟢 Sniper ativo" if ativo else "🔴 Sniper parado"
    await update.message.reply_text(msg)


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    up = int(time.time() - context.bot_data.get("start_time", time.time()))
    await update.message.reply_text(
        f"pong 🏓\n⏱ Uptime: {str(datetime.timedelta(seconds=up))}"
    )


async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = int(TELEGRAM_CHAT_ID)
        ts      = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid     = uuid.uuid4().hex[:8]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Teste! 🕒 {ts}\nID: `{uid}`",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"Mensagem enviada (ID={uid})")
    except Exception as e:
        logger.error("Erro em /testnotify: %s", e, exc_info=True)
        await update.message.reply_text("⚠️ Falha na notificação.")


async def relatorio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Relatório de eventos não implementado.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")


# --- Startup ---
if __name__ == "__main__":
    # 1. Validações iniciais
    if not TELEGRAM_TOKEN:
        logger.error("Falta TELEGRAM_TOKEN. Abortando.")
        raise SystemExit(1)
    validate_sniper_config()

    try:
        logger.info("🔑 Carteira ativa: %s", get_active_address())
    except Exception as e:
        logger.error("Chave inválida: %s", e, exc_info=True)
        raise SystemExit(1)

    # 2. Prepara loop e Application do PTB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 3. Registra handlers
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

    # 4. Inicializa e inicia o bot
    loop.run_until_complete(application.initialize())
    loop.run_until_complete(application.start())

    # 5. Define comandos no Telegram
    loop.run_until_complete(
        application.bot.set_my_commands([
            BotCommand("start",        "🎯 Bem-vindo"),
            BotCommand("menu",         "📋 Menu"),
            BotCommand("status",       "💰 Saldo"),
            BotCommand("snipe",        "🟢 Iniciar sniper"),
            BotCommand("stop",         "🔴 Parar sniper"),
            BotCommand("sniperstatus", "📈 Status sniper"),
            BotCommand("ping",         "🏓 Ping"),
            BotCommand("testnotify",   "🛰️ Teste notificação"),
            BotCommand("relatorio",    "📊 Relatório")
        ])
    )

    # 6. Registra webhook junto ao Telegram
    if WEBHOOK_URL:
        loop.run_until_complete(application.bot.set_webhook(WEBHOOK_URL))
        logger.info("✅ Webhook registrado em %s", WEBHOOK_URL)
    else:
        logger.warning("WEBHOOK_URL não definido, pulei set_webhook().")

    # 7. Roda o loop do bot em background
    Thread(target=loop.run_forever, daemon=True).start()
    logger.info("🚀 Bot Telegram rodando em thread separada.")

    # 8. Inicia o Flask com suas rotas (/api/*, /webhook)
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
