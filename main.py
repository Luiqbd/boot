import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler
from threading import Thread
import time

# --- Configuração de log ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

app = Flask(__name__)

# --- Variáveis globais ---
loop = None
application = None

# --- Configurações via variáveis de ambiente ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

# --- Handler /start ---
async def start_cmd(update: Update, context):
    await update.message.reply_text(
        "Olá, eu estou vivo 🚀! Pode me enviar comandos e mensagens que eu já respondo."
    )

# --- Endpoint do webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    global loop, application
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

# --- Registro do webhook no Telegram ---
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    resp = requests.post(url, json={"url": WEBHOOK_URL})
    if resp.status_code == 200 and resp.json().get("ok"):
        logging.info(f"✅ Webhook registrado com sucesso: {WEBHOOK_URL}")
    else:
        logging.error(f"❌ Falha ao registrar webhook: {resp.text}")

# --- Função para iniciar Flask e registrar depois ---
def run_flask():
    # Espera 2 segundos para garantir que o Render levantou o serviço
    def delayed_webhook():
        time.sleep(2)
        set_webhook()

    Thread(target=delayed_webhook).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Inicialização principal ---
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))

    loop.create_task(application.initialize())
    loop.create_task(application.start())

    logging.info("🚀 Bot iniciado, iniciando servidor Flask e preparando registro do webhook...")
    run_flask()
