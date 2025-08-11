import logging
import time
import threading
import os

from flask import Flask
from strategy import TradingStrategy
from dex import DexClient
from config import config

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes


# Configuração do logger
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("🔍 main.py iniciado — verificação de instância única")
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
    await update.message.reply_text("Fala, Luis! Seu bot tá online no Telegram 🚀")

# 🚀 Inicialização
if __name__ == "__main__":
    # Inicia o bot de trading em uma thread
    trading_thread = threading.Thread(target=executar_bot)
    trading_thread.daemon = True
    trading_thread.start()

    # Inicia o bot do Telegram
    TOKEN = os.getenv("TELEGRAM_TOKEN")
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    logger.info("Bot do Telegram iniciado 🔵")
    app.run_polling()
