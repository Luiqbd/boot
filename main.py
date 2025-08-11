from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram import Update
import threading, time, os, logging
from strategy import TradingStrategy
from dex import DexClient
from config import config

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
            logger.error("Erro durante execução: %s", str(e))
        time.sleep(config['INTERVAL'])

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala, Luis! Seu bot tá online via webhook 🚀")

# Telegram bot
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = "https://boot-no4o.onrender.com/webhook"
PORT = int(os.environ.get("PORT", 5000))

telegram_app = ApplicationBuilder().token(TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start))

if __name__ == "__main__":
    threading.Thread(target=executar_bot, daemon=True).start()
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL
    )
