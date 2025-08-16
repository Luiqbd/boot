import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler

# --- Configuração de log ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Flask app ---
app = Flask(__name__)

# --- Variáveis globais ---
loop = None
application = None

# --- Configurações ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "SEU_TOKEN_AQUI")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://SEU-PROJETO.onrender.com/webhook")

# --- Handler /start ---
async def start_cmd(update: Update, context):
    await update.message.reply_text(
        "Olá, eu estou vivo 🚀! Pode me enviar comandos e mensagens que eu já respondo."
    )

# --- Webhook ---
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

# --- Registro do Webhook no Telegram ---
def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    resp = requests.post(url, json={"url": WEBHOOK_URL})
    if resp.status_code == 200 and resp.json().get("ok"):
        logging.info(f"✅ Webhook registrado com sucesso: {WEBHOOK_URL}")
    else:
        logging.error(f"❌ Falha ao registrar webhook: {resp.text}")

# --- Inicialização ---
if __name__ == "__main__":
    # Criar loop global
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Criar Application do Telegram
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Adicionar handlers
    application.add_handler(CommandHandler("start", start_cmd))

    # Iniciar bot no loop
    loop.create_task(application.initialize())
    loop.create_task(application.start())

    # Registrar webhook
    set_webhook()

    logging.info("🚀 Bot iniciado e pronto para receber webhooks")

    # Iniciar servidor Flask
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
