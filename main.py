from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update
import threading, time, os, logging
from strategy import TradingStrategy
from dex import DexClient
from config import config

# Logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Bot de trading
def executar_bot():
    logger.info("Bot de trading iniciado 🚀")
    dex = DexClient(config['RPC_URL'], config['PRIVATE_KEY'])
    strategy = TradingStrategy(dex)
    while True:
        try:
            logger.info("Executando estratégia...")
            strategy.run()
        except Exception as e:
            logger.error("Erro na estratégia: %s", str(e))
        time.sleep(config['INTERVAL'])

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala, Luis! Seu bot tá online via webhook 🚀")

# Telegram bot
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = "https://boot-no4o.onrender.com"
PORT_FLASK = int(os.environ.get("PORT", 5000))
PORT_TELEGRAM = 8443  # Porta separada pro webhook do bot

telegram_app = ApplicationBuilder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))

# Flask app
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return '✅ Bot está rodando com Flask + Webhook Telegram'

@flask_app.route('/status')
def status():
    return '🔍 Status: Estratégia ativa, Telegram aguardando comandos.'

# Flask em thread separada
def iniciar_flask():
    flask_app.run(host="0.0.0.0", port=PORT_FLASK)

# Telegram webhook em thread separada
def iniciar_telegram():
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT_TELEGRAM,
        webhook_url=WEBHOOK_URL
    )

# Inicialização geral
if __name__ == "__main__":
    threading.Thread(target=executar_bot, daemon=True).start()
    threading.Thread(target=iniciar_flask, daemon=True).start()
    iniciar_telegram()
