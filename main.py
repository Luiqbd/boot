import os
import time
import threading
import asyncio
import logging

from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from strategy import TradingStrategy
from dex import DexClient
from config import config

# ----------------------
# Logging
# ----------------------
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ----------------------
# Telegram Application
# ----------------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN não definido no ambiente")

PUBLIC_BASE_URL = "https://boot-no4o.onrender.com"  # sua URL pública no Render
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

application = ApplicationBuilder().token(TOKEN).build()

# Vamos guardar o loop do bot para uso no Flask
telegram_loop: asyncio.AbstractEventLoop | None = None

# ----------------------
# Handlers do Telegram
# ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala, Luis! Seu bot tá online via webhook 🚀")

application.add_handler(CommandHandler("start", start))

# ----------------------
# Estratégia de trading (thread separada)
# ----------------------
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

# ----------------------
# Inicialização assíncrona do Telegram (loop próprio)
# ----------------------
def iniciar_telegram():
    global telegram_loop

    async def runner():
        # Inicializa e inicia o Application
        await application.initialize()
        await application.start()

        # Configura o webhook apontando para o Flask
        await application.bot.set_webhook(WEBHOOK_URL)
        logger.info("Webhook configurado em %s", WEBHOOK_URL)

        # Mantém vivo
        while True:
            await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    telegram_loop = loop
    try:
        loop.run_until_complete(runner())
    finally:
        loop.run_until_complete(application.stop())
        loop.run_until_complete(application.shutdown())
        loop.close()

# ----------------------
# Flask (servidor público)
# ----------------------
flask_app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))

@flask_app.route("/", methods=["GET", "HEAD", "POST"])
def home():
    # Evita 405 nos logs quando alguém (ou o Telegram) posta na raiz sem querer
    if request.method == "POST":
        return "ignored", 200
    return "✅ Bot está rodando com Flask + Telegram Webhook"

@flask_app.route("/status", methods=["GET"])
def status():
    return "🔍 Status: Estratégia ativa e Telegram aguardando comandos."

@flask_app.route(WEBHOOK_PATH, methods=["POST", "GET"])
def webhook():
    # Telegram envia POST. Devolvemos 200 a GET para evitar 405 acidental.
    if request.method == "GET":
        return "OK", 200
    try:
        data = request.get_json(force=True, silent=False)
        update = Update.de_json(data, application.bot)
        if telegram_loop is None:
            logger.error("Loop do Telegram ainda não inicializado")
            return "Loop não pronto", 503

        # Empurra a atualização para o Application no loop assíncrono
        fut = asyncio.run_coroutine_threadsafe(
            application.update_queue.put(update),
            telegram_loop
        )
        fut.result(timeout=2)  # opcional: espera curto só pra detectar falhas
        return "OK", 200
    except Exception as e:
        logger.exception("Erro ao processar webhook: %s", e)
        return "Erro interno", 500

def iniciar_flask():
    # Render expõe somente a PORT pública. O Flask deve rodar nessa porta.
    flask_app.run(host="0.0.0.0", port=PORT)

# ----------------------
# Main
# ----------------------
if __name__ == "__main__":
    # Thread da estratégia
    threading.Thread(target=executar_bot, daemon=True).start()
    # Thread do Flask (porta pública)
    threading.Thread(target=iniciar_flask, daemon=True).start()
    # Thread do Telegram (loop assíncrono + setWebhook)
    iniciar_telegram()
