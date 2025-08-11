import logging
import time
import threading
import os

from flask import Flask, request
from strategy import TradingStrategy
from dex import DexClient
from config import config

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Configuração do logger
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("🔍 main.py iniciado — webhook ativado")

# 🔁 Função do bot de trading
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

# 💬 Comando do Telegram
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala, Luis! Seu bot tá online via webhook 🚀")

# 🚀 Inicialização
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = "https://boot-no4o.onrender.com/webhook"

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return 'Bot está rodando com webhook!'

@app_flask.route('/webhook', methods=['POST'])
def webhook():
    return telegram_app.update_webhook(request)

if __name__ == "__main__":
    # Inicia o bot de trading em uma thread
    trading_thread = threading.Thread(target=executar_bot)
    trading_thread.daemon = True
    trading_thread.start()

    # Inicia o bot do Telegram com webhook
    telegram_app = ApplicationBuilder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))

    # Configura o webhook no Telegram
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        webhook_url=WEBHOOK_URL
    )

    # Inicia o Flask (Render precisa disso para manter o serviço vivo)
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
