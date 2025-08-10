import logging
import time
from strategy import TradingStrategy
from dex import DexClient
from config import config

# Configuração do logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
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
    main()
