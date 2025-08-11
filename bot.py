import logging
import time
import threading
from flask import Flask
from strategy import TradingStrategy
from dex import DexClient
from config import config

# Configuração do logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cria o app Flask
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de trading está rodando! 🟢"

def executar_bot():
    logger.info("Bot iniciado 🚀")
    dex = DexClient(config['RPC_URL'], config['PRIVATE_KEY'])
    strategy = TradingStrategy(dex)

    while True:
        try:
            logger.info("Executando estratégia...")
            strategy.run()
        except Exception as e:
            logger.error("Erro durante execução: %s", str(e))
        time.sleep(config['INTERVAL'])

if __name__ == "__main__":
    # Inicia o bot em uma thread separada
    bot_thread = threading.Thread(target=executar_bot)
    bot_thread.daemon = True
    bot_thread.start()

    # Inicia o servidor Flask
    import os
app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))


