from strategy import TradingStrategy
from dex import DexClient
from config import config
import time

def main():
    dex = DexClient(config['RPC_URL'], config['PRIVATE_KEY'])
    strategy = TradingStrategy(dex)

    while True:
        try:
            strategy.run()
        except Exception as e:
            print(f"Erro: {e}")
        time.sleep(config['INTERVAL'])

if __name__ == "__main__":
    main()