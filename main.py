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
from web3 import Web3

# --- Importações sniper ---
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status

# --- Configuração de log ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Flask app ---
app = Flask(__name__)

# --- Variáveis globais ---
loop = asyncio.new_event_loop()
application = None
sniper_thread = None

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")  # usado no /testnotify

# --- Funções auxiliares ---
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
        f"🛑 Stop Loss: {os.getenv('STOP_LOSS_PCT')}%\n"
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')}%\n"
        f"💧 Min. Liquidez WETH: {os.getenv('MIN_LIQ_WETH')}\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')}s\n"
    )

# --- Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = (
        "🎯 **Bem-vindo ao Sniper Bot Criado por Luis Fernando**\n\n"
        "📌 **Comandos disponíveis**\n"
        "🟢 /snipe — Inicia o sniper e começa a monitorar.\n"
        "🔴 /stop — Para o sniper imediatamente.\n"
        "📈 /sniperstatus — Consulta status do sniper.\n"
        "💰 /status  — Mostra saldo ETH/WETH.\n"
        "🏓 /ping — Confirma se o bot está online.\n"
        "🛰️ /testnotify — Envia mensagem de teste para o chat configurado.\n"
        "📜 /menu — Reexibe esta mensagem.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛠 **Configuração Atual**\n"
        f"{env_summary_text()}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
       
    )
    await update.message.reply_text(mensagem, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wallet_address = context.args[0] if context.args else None
        status = get_wallet_status(wallet_address)
        await update.message.reply_text(status)
    except Exception as e:
        logging.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status da carteira.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return
    await update.message.reply_text("🎯 Iniciando sniper... Monitorando novos pares com liquidez.")

    def start_sniper():
        try:
            run_discovery(lambda pair, t0, t1: on_new_pair(pair, t0, t1, bot=application.bot))
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_discovery()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status()
        await update.message.reply_text(status["text"])
    except Exception as e:
        logging.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status do sniper.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

# --- Novos comandos de diagnóstico ---
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde 'pong' para confirmar que o bot está online"""
    await update.message.reply_text("pong 🏓")

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia uma mensagem de teste para o chat configurado via TELEGRAM_CHAT_ID"""
    try:
        chat_id_str = TELEGRAM_CHAT_ID or "0"
        chat_id = int(chat_id_str) if chat_id_str.isdigit() else 0
        if chat_id == 0:
            await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID ausente ou inválido nas variáveis de ambiente.")
            return

        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Teste de notificação: seu sniper está pronto para narrar as operações!"
        )
        await update.message.reply_text("Mensagem de teste enviada para o chat configurado.")
    except Exception as e:
        logging.error(f"Erro no /testnotify: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Erro ao enviar mensagem: {e}")

# --- Healthcheck ---
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if application is None:
            return 'not ready', 503
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        return 'ok', 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info=True)
        return 'error', 500

def set_webhook_with_retry(max_attempts=5, delay=3):
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logging.error("WEBHOOK não configurado: faltam TELEGRAM_TOKEN ou WEBHOOK_URL.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, json={"url": WEBHOOK_URL}, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                logging.info(f"✅ Webhook registrado com sucesso: {WEBHOOK_URL}")
                return
            logging.warning(f"Tentativa {attempt} falhou: {resp.text}")
        except Exception as e:
            logging.warning(f"Tentativa {attempt} lançou exceção: {e}")
        time.sleep(delay)
    logging.error("❌ Todas as tentativas de registrar o webhook falharam.")

def start_flask():
    port = int(os.environ.get("PORT", 10000))
    # threaded=True para lidar bem com múltiplas conexões
    app.run(host="0.0.0.0", port=port, threaded=True)

# --- Inicialização ---
if __name__ == "__main__":
    # Validação básica de env
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN no ambiente. Encerrando.")
        raise SystemExit(1)
    if not WEBHOOK_URL:
        logging.warning("WEBHOOK_URL não definido. O webhook não será registrado automaticamente.")

    asyncio.set_event_loop(loop)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))                 # novo
    application.add_handler(CommandHandler("testnotify", test_notify_cmd))    # novo
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    async def start_bot():
        await application.initialize()
        await application.start()
        # Registra o “menu /” do Telegram com todos os comandos
        await application.bot.set_my_commands([
            BotCommand("start", "Mostra boas-vindas e configuração"),
            BotCommand("menu", "Reexibe o menu"),
            BotCommand("status", "Mostra saldo ETH/WETH da carteira"),
            BotCommand("snipe", "Inicia o sniper"),
            BotCommand("stop", "Para o sniper"),
            BotCommand("sniperstatus", "Status do sniper"),
            BotCommand("ping", "Teste de vida (pong)"),
            BotCommand("testnotify", "Envia uma notificação de teste")
        ])

    loop.create_task(start_bot())
    flask_thread = Thread(target=start_flask, daemon=True)
    flask_thread.start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
