# main.py
import os
import sys
import time
import uuid
import logging
import requests
import datetime
import asyncio

from threading import Thread
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from web3 import Web3

from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from risk_manager import RiskManager

# --- Configuração de Logging ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# Debug prints para capturar variáveis de ambiente
print("🔥 main.py iniciado", file=sys.stderr)
for var in ("TELEGRAM_TOKEN", "RPC_URL", "PRIVATE_KEY", "CHAIN_ID", "WEBHOOK_URL"):
    print(f"{var} =", bool(os.getenv(var)), file=sys.stderr)

# --- Validação de variáveis de ambiente ---

def validate_env():
    required = ["TELEGRAM_TOKEN", "RPC_URL", "PRIVATE_KEY", "CHAIN_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logging.error(f"Variáveis de ambiente faltando: {', '.join(missing)}")
        raise SystemExit(1)

validate_env()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# --- Import dinâmico de discovery wrappers ---

import discovery as _discovery

try:
    run_discovery         = _discovery.run_discovery
    stop_discovery        = _discovery.stop_discovery
    get_discovery_status  = _discovery.get_discovery_status
except AttributeError:
    available = [n for n in dir(_discovery) if not n.startswith("_")]
    logging.error(
        "Módulo discovery.py não exporta run_discovery/stop_discovery/get_discovery_status. "
        "Funções disponíveis: %s", available
    )
    raise SystemExit(1)

# --- Utilitários e validações ---

def str_to_bool(v: str) -> bool:
    return v.strip().lower() in {"1","true","t","yes","y"}

def normalize_private_key(pk: str) -> str:
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("PRIVATE_KEY inválida")
    return pk

def get_active_address() -> str:
    raw = os.getenv("PRIVATE_KEY")
    return Web3().eth.account.from_key(normalize_private_key(raw)).address

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

# --- Estado e controle do Sniper ---

risk_manager   = RiskManager()
loop           = asyncio.new_event_loop()
application    = None
sniper_thread  = None

def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ Sniper já está ativo.")
        return

    def runner():
        coro = run_discovery(
            lambda dex, pair, t0, t1: on_new_pair(
                dex, pair, t0, t1, bot=application.bot, loop=loop
            ),
            loop
        )
        asyncio.run_coroutine_threadsafe(coro, loop)

    sniper_thread = Thread(target=runner, daemon=True)
    sniper_thread.start()
    logging.info("⚙️ Sniper iniciado.")

def parar_sniper():
    stop_discovery(loop)
    logging.info("🛑 Sniper parado.")

# --- Handlers Telegram ---

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    menu = (
        "🎯 Sniper Bot por Luis Fernando\n\n"
        "🟢 /snipe — Inicia sniper\n"
        "🔴 /stop — Para sniper\n"
        "📈 /sniperstatus — Status sniper\n"
        "💰 /status [addr] — Saldo ETH/WETH\n"
        "🏓 /ping — Uptime\n"
        "🛰️ /testnotify — Notify teste\n"
        "📜 /menu — Menu\n"
        "📊 /relatorio — Relatório de risco\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛠 Configuração:\n"
        f"{env_summary_text()}\n"
    )
    await update.message.reply_text(menu, parse_mode="Markdown")

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    addr = ctx.args[0] if ctx.args else None
    try:
        await update.message.reply_text(get_wallet_status(addr))
    except Exception:
        await update.message.reply_text("⚠️ Erro ao verificar status.")

async def snipe_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if sniper_thread and sniper_thread.is_alive():
        return await update.message.reply_text("⚠️ Já rodando.")
    iniciar_sniper()
    await update.message.reply_text("⚙️ Sniper iniciado.")

async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    status = get_discovery_status() or {"text": "Indisponível."}
    await update.message.reply_text(status["text"])

async def ping_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    start_ts = ctx.bot_data.get("start_time", time.time())
    uptime = str(datetime.timedelta(seconds=int(time.time() - start_ts)))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"🏓 pong\n⏱ Uptime: {uptime}\n🕒 Agora: {now}")

async def test_notify_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0
    if cid == 0:
        return await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID inválido.")
    ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = uuid.uuid4().hex[:8]
    text = f"✅ Teste\n🕒 {ts}\n🆔 {uid}"
    await ctx.bot.send_message(chat_id=cid, text=text)
    await update.message.reply_text(f"Enviado (ID {uid})")

async def relatorio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rpt = risk_manager.gerar_relatorio() or "Sem eventos."
    await update.message.reply_text(f"📊 Relatório:\n{rpt}")

async def echo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

# --- Flask Endpoints ---

flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

@flask_app.route("/relatorio", methods=["GET"])
def relatorio_http():
    try:
        rpt = risk_manager.gerar_relatorio() or "Sem eventos."
        return f"<h1>📊 Relatório</h1><pre>{rpt}</pre>"
    except Exception as e:
        logging.error(f"HTTP relatório failed: {e}", exc_info=True)
        return "Erro", 500

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if application is None:
        return "not ready", 503
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
    return "ok", 200

# --- Webhook Setup ---

def set_webhook_with_retry(url: str, token: str, tries=5, delay=3):
    api = f"https://api.telegram.org/bot{token}/setWebhook"
    payload = {"url": url}
    for i in range(tries):
        try:
            resp = requests.post(api, json=payload, timeout=10)
            if resp.ok and resp.json().get("ok"):
                logging.info(f"✅ Webhook registrado: {url}")
                return
            logging.warning(f"Tentativa {i+1} falhou: {resp.text}")
        except Exception as e:
            logging.warning(f"Tentativa {i+1} exception: {e}")
        time.sleep(delay)
    logging.error("❌ Falha ao registrar webhook.")

# --- Bootstrapping ---

def main():
    global application

    # Seta o loop e cria a aplicação Telegram
    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registra comandos e handlers
    commands = [
        ("start", start_cmd),
        ("menu", start_cmd),
        ("status", status_cmd),
        ("snipe", snipe_cmd),
        ("stop", stop_cmd),
        ("sniperstatus", sniper_status_cmd),
        ("ping", ping_cmd),
        ("testnotify", test_notify_cmd),
        ("relatorio", relatorio_cmd),
    ]
    for cmd, handler in commands:
        application.add_handler(CommandHandler(cmd, handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Inicialização assíncrona do bot
    async def boot_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand(cmd, desc) for cmd, desc in [
                ("start", "Boas-vindas e configuração"),
                ("menu", "Reexibe menu"),
                ("status", "Saldo ETH/WETH"),
                ("snipe", "Inicia sniper"),
                ("stop", "Para sniper"),
                ("sniperstatus", "Status sniper"),
                ("ping", "Teste de vida"),
                ("testnotify", "Notificação teste"),
                ("relatorio", "Relatório de risco"),
            ]
        ])

    loop.create_task(boot_bot())

    # Sobe servidor Flask em thread
    Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=int(os.getenv("PORT", "10000"))
        ),
        daemon=True
    ).start()

    # Registra webhook se configurado
    if WEBHOOK_URL:
        Thread(
            target=lambda: set_webhook_with_retry(WEBHOOK_URL, TELEGRAM_TOKEN),
            daemon=True
        ).start()
    else:
        logging.warning("WEBHOOK_URL não definido; webhook não será registrado.")

    logging.info("🚀 Bot e Flask rodando.")
    loop.run_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.exception("🚨 Erro não tratado na inicialização do bot:")
        raise
