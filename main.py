import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from threading import Thread
import time

# --- Importações sniper ---
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery

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

# --- Configurações via ambiente ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

# --- Handler /start ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá, eu estou vivo 🚀! Use /snipe para iniciar o sniper ou /stop para parar.")

# --- Handler /status ---
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if context.args:
            wallet_address = context.args[0]
            status = get_wallet_status(wallet_address)
            await update.message.reply_text(status)
        else:
            await update.message.reply_text("❗ Você precisa informar o endereço da carteira.\nExemplo: /status 0x123...")
    except Exception as e:
        logging.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Ocorreu um erro ao verificar o status da carteira.")

# --- Handler /snipe ---
async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return

    await update.message.reply_text("🎯 Iniciando sniper... Monitorando novos pares com liquidez.")
    sniper_thread = Thread(target=run_discovery, args=(on_new_pair,))
    sniper_thread.start()

# --- Handler /stop ---
async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_discovery()
    await update.message.reply_text("🛑 Sniper interrompido.")

# --- Handler para mensagens comuns ---
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

# --- Endpoint do webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            loop
        )
        return 'ok', 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info=True)
        return 'error', 500

# --- Registro do webhook com retry ---
def set_webhook_with_retry(max_attempts=5, delay=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(url, json={"url": WEBHOOK_URL})
        if resp.status_code == 200 and resp.json().get("ok"):
            logging.info(f"✅ Webhook registrado com sucesso: {WEBHOOK_URL}")
            return
        logging.warning(f"Tentativa {attempt} falhou: {resp.text}")
        time.sleep(delay)
    logging.error("❌ Todas as tentativas de registrar o webhook falharam.")

# --- Iniciar Flask em thread separada ---
def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Inicialização principal ---
if __name__ == "__main__":
    asyncio.set_event_loop(loop)

    # Criar bot Telegram
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Iniciar bot no loop principal
    loop.create_task(application.initialize())
    loop.create_task(application.start())

    # Iniciar Flask em thread separada
    flask_thread = Thread(target=start_flask)
    flask_thread.start()

    # Registrar webhook com retry
    Thread(target=set_webhook_with_retry).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
