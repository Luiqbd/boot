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
from utils import escapemdv2
from discovery import subscribenewpairs, stopdiscovery, isdiscovery_running
from exchange_client import ExchangeClient
from riskmanager import riskmanager
from strategysniper import onnew_pair
from tokenservice import gerarmeutokenexterno
from checkbalance import getwallet_status

─── Configurações básicas ──────────────────────────────────────────────
RPCURL    = config["RPCURL"]
CHAINID   = int(config["CHAINID"])
TELETOKEN = config["TELEGRAMTOKEN"]
TELECHAT  = config["TELEGRAMCHAT_ID"]
PORT       = int(os.getenv("PORT", 10000))

─── Logger ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(name)

─── Conexão Web3 e verificação de DEX ──────────────────────────────────
web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not web3.is_connected():
    logger.error("Falha ao conectar no RPC %s", RPC_URL)
    sys.exit(1)

if not config["DEXES"]:
    logger.error("Nenhuma DEX configurada. Verifique variáveis DEX1…")
    sys.exit(1)

exchange_client = ExchangeClient(config["DEXES"][0].router)

─── Telegram Bot Setup ──────────────────────────────────────────────────
telegramloop = asyncio.newevent_loop()
asyncio.seteventloop(telegram_loop)

application = ApplicationBuilder().token(TELE_TOKEN).build()
app_bot = application.bot
application.botdata["starttime"] = time.time()

async def startcmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    texto = (
        "🎯 Sniper Bot\n\n"
        "/snipe — iniciar sniper\n"
        "/stop — parar sniper\n"
        "/sniperstatus — status sniper\n"
        "/status — saldo ETH/WETH\n"
        "/ping — alive check\n"
        "/testnotify — notificação teste\n"
        "/menu — este menu\n"
        "/relatorio — relatório de eventos\n\n"
        "Config atual:\n"
        f"{envsummarytext()}"
    )
    await update.message.replymarkdownv2(texto)

async def snipecmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    await update.message.reply_text(
        "⚙️ Iniciando sniper...", parse_mode="MarkdownV2"
    )
    iniciar_sniper()

async def stopcmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    parar_sniper()
    await update.message.reply_text(
        "🛑 Sniper interrompido.", parse_mode="MarkdownV2"
    )

async def sniperstatuscmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = "🟢 Ativo" if isdiscoveryrunning() else "🔴 Parado"
    await update.message.reply_text(msg)

async def statuscmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    addr = ctx.args[0] if ctx.args else None
    bal = getwalletstatus(addr)
    await update.message.reply_text(bal)

async def pingcmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    up = int(time.time() - ctx.botdata["starttime"])
    await update.message.reply_text(
        f"pong 🏓\n⏱ Uptime: {datetime.timedelta(seconds=up)}"
    )

async def testnotifycmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = uuid.uuid4().hex[:6]
    text = f"✅ Teste 🕒{ts}\nID: {uid}"
    await appbot.sendmessage(
        chatid=TELECHAT, text=text, parse_mode="MarkdownV2"
    )
    await update.message.reply_text(f"Enviado (ID={uid})")

async def relatoriocmd(update: Update, ctx: ContextTypes.DEFAULTTYPE):
    report = riskmanager.gerarrelatorio()
    await update.message.reply_text(report)

async def echo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = escapemdv2(update.message.text)
    await update.message.reply_text(f"Você disse: {txt}")

Registra handlers
cmds = [
    ("start", start_cmd),
    ("menu", start_cmd),
    ("snipe", snipe_cmd),
    ("stop", stop_cmd),
    ("sniperstatus", sniperstatuscmd),
    ("status", status_cmd),
    ("ping", ping_cmd),
    ("testnotify", testnotify_cmd),
    ("relatorio", relatorio_cmd),
]
for name, handler in cmds:
    application.add_handler(CommandHandler(name, handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

telegramloop.rununtil_complete(
    appbot.setmycommands([BotCommand(n, h.doc_ or "") for n, h in cmds])
)

Thread(target=telegramloop.runforever, daemon=True).start()
logger.info("🛰️ Telegram bot rodando em background")

─── Discovery / Sniper Orquestração ────────────────────────────────────
def iniciar_sniper():
    if isdiscoveryrunning():
        logger.info("⚠️ Sniper já ativo")
        return

    def cb(pairaddress, token0, token1, dex_info):
        asyncio.runcoroutinethreadsafe(
            onnewpair(dexinfo, pairaddress, token0, token1),
            telegram_loop
        )

    subscribenewpairs(callback=_cb)
    logger.info("🟢 Sniper iniciado")

def parar_sniper():
    stop_discovery()
    logger.info("🔴 Sniper parado")

def envsummarytext() -> str:
    addr = web3.eth.account.fromkey(config["PRIVATEKEY"]).address
    return (
        f"🔑 {addr}\n"
        f"🌐 Chain ID: {CHAIN_ID}\n"
        f"🔗 RPC: {RPC_URL}\n"
        f"⏱ Disc Interval: {config['DISCOVERY_INTERVAL']}s\n"
        f"🧪 Dry Run: {config['DRY_RUN']}"
    )

def fetch_token() -> str:
    try:
        t = gerarmeutoken_externo()
        logger.info("✅ Token Auth0 obtido")
        return t
    except Exception as e:
        logger.error("❌ Erro Auth0: %s", e, exc_info=True)
        return ""

─── Flask API ───────────────────────────────────────────────────────────
app = Flask(name)

@app.route("/api/token", methods=["GET"])
def api_token():
    t = fetch_token()
    if not t:
        return jsonify({"error": "Auth0 fail"}), 502
    return jsonify({"token": t})

def require_auth(f):
    @wraps(f)
    def inner(args, *kwargs):
        hdr = request.headers.get("Authorization", "")
        if not hdr.lower().startswith("bearer "):
            abort(401)
        # TODO: validar JWT contra Auth0
        return f(args, *kwargs)
    return inner

@app.route("/api/status", methods=["GET"])
@require_auth
def api_status():
    return jsonify({"sniperactive": isdiscovery_running()})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data or "message" not in data:
        return "ignored", 200

    upd = Update.dejson(data, appbot)
    asyncio.runcoroutinethreadsafe(
        application.processupdate(upd), telegramloop
    )
    return "ok", 200

─── Shutdown Graceful ───────────────────────────────────────────────────
def _shutdown(signum, frame):
    logger.info("Recebido signal %s, encerrando...", signum)
    parar_sniper()
    asyncio.run(application.shutdown())
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, _shutdown)

─── Entry Point ─────────────────────────────────────────────────────────
if name == "main":
    try:
         = web3.eth.account.fromkey(config["PRIVATE_KEY"]).address
    except Exception as e:
        logger.error("PRIVATE_KEY inválida: %s", e)
        sys.exit(1)

    logger.info("🚀 Iniciando Flask API na porta %s", PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
