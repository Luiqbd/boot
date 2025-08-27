# main.py

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

from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status
from config import config
from risk_manager import RiskManager

# ── DECLARAÇÃO GLOBAL PARA CACHE DE PARES ─────────────────────────────
# Evita NameError e armazena timestamps / listas de eventos para cada par
_recent_pairs: dict[str, list] = {}

# ── Risk Manager ───────────────────────────────────────────────────────
risk_manager = RiskManager()

# ── Configuração de log ─────────────────────────────────────────────────
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# ── Flask app ──────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Variáveis globais do bot ────────────────────────────────────────────
loop = asyncio.new_event_loop()
application = None
sniper_thread = None

# ── Variáveis de ambiente ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# ── Funções auxiliares ──────────────────────────────────────────────────
def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY não definida no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("PRIVATE_KEY inválida: formato incorreto.")
    return pk

def get_active_address() -> str:
    pk_raw = os.getenv("PRIVATE_KEY")
    pk = normalize_private_key(pk_raw)
    return Web3().eth.account.from_key(pk).address

def env_summary_text() -> str:
    try:
        addr = get_active_address()
    except Exception as e:
        addr = f"Erro ao obter: {e}"

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

# ── Inicia e para o sniper ──────────────────────────────────────────────
def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return

    logging.info("⚙️ Iniciando sniper... Monitorando novos pares.")

    def _run():
        try:
            asyncio.run_coroutine_threadsafe(
                run_discovery(
                    lambda dex, pair, t0, t1: on_new_pair(
                        dex, pair, t0, t1, bot=application.bot, loop=loop
                    ),
                    loop
                ),
                loop
            )
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=_run, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery(loop)

# ── Handlers do Telegram ─────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🎯 *Sniper Bot*\n\n"
        "• /snipe — Inicia o sniper\n"
        "• /stop — Para o sniper\n"
        "• /sniperstatus — Status do sniper\n"
        "• /status — Saldo ETH/WETH\n"
        "• /ping — Pong\n"
        "• /testnotify — Teste de notificação\n"
        "• /menu — Reexibe este menu\n"
        "• /relatorios — Gera relatório\n\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_text(texto, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        addr = context.args[0] if context.args else None
        st = get_wallet_status(addr)
        await update.message.reply_text(st)
    except Exception as e:
        logging.error(f"/status erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar carteira.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ Sniper já está ativo.")
        return
    await update.message.reply_text("⚙️ Iniciando sniper…")
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
        await update.message.reply_text("⚠️ Erro no status do sniper.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - context.bot_data.get("start_time", time.time()))
    up_str = str(datetime.timedelta(seconds=uptime))
    now   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"pong 🏓\nUptime: {up_str}\nAgora: {now}")

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = int(TELEGRAM_CHAT_ID or 0)
    if chat_id == 0:
        await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID inválido.")
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uid = str(uuid.uuid4())[:8]
    await context.bot.send_message(chat_id=chat_id, text=f"✅ Teste: {ts} ID:{uid}")
    await update.message.reply_text(f"Teste enviado (ID: {uid})")

async def relatorio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rel = risk_manager.gerar_relatorio()
        await update.message.reply_text(f"📊 Relatório de Eventos:\n{rel}")
    except Exception as e:
        logging.error(f"/relatorios erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao gerar relatório.")

# ── HTTP Endpoints ──────────────────────────────────────────────────────
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

@app.route("/relatorios", methods=["GET"])
def relatorios_http():
    try:
        rel = risk_manager.gerar_relatorio()
        return f"<h1>📊 Relatório de Eventos</h1><pre>{rel}</pre>"
    except Exception as e:
        logging.error(f"Erro HTTP /relatorios: {e}", exc_info=True)
        return "Erro ao gerar relatório", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        if application is None:
            return "not ready", 503
        data   = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        return "ok", 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info=True)
        return "error", 500

def set_webhook_with_retry(attempts=5, delay=3):
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logging.error("WEBHOOK não configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for i in range(1, attempts+1):
        try:
            resp = requests.post(url, json={"url": WEBHOOK_URL}, timeout=10)
            if resp.ok and resp.json().get("ok"):
                logging.info(f"Webhook registrado: {WEBHOOK_URL}")
                return
        except Exception as e:
            logging.warning(f"Tentativa {i} falhou: {e}")
        time.sleep(delay)
    logging.error("Todas as tentativas de registrar webhook falharam.")

def start_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# ── Boot do Bot ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Verifica variáveis essenciais
    missing = [k for k in ("TELEGRAM_TOKEN","RPC_URL","PRIVATE_KEY","CHAIN_ID") if not os.getenv(k)]
    if missing:
        logging.error(f"Faltam variáveis de ambiente: {missing}")
        raise SystemExit(1)

    # Valida chave e inicializa client
    try:
        addr = get_active_address()
        logging.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Chave inválida: {e}", exc_info=True)
        raise SystemExit(1)

    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("testnotify", test_notify_cmd))
    application.add_handler(CommandHandler("relatorios", relatorio_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Inicia bot, Flask e registra webhook
    async def start_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand("start",       "Boas-vindas"),
            BotCommand("menu",        "Reexibe menu"),
            BotCommand("status",      "Saldo ETH/WETH"),
            BotCommand("snipe",       "Inicia sniper"),
            BotCommand("stop",        "Para sniper"),
            BotCommand("sniperstatus","Status sniper"),
            BotCommand("ping",        "Pong"),
            BotCommand("testnotify",  "Teste notify"),
            BotCommand("relatorios",  "Relatório eventos")
        ])

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
