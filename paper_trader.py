import logging

logger = logging.getLogger(__name__)

class PaperTrader:
    def __init__(self, web3, private_key):
        self.web3 = web3
        self.private_key = private_key
        self.simulated_balance = {
            "ETH": 1.0,
            "TOSHI": 0.0
        }

    def buy(self, token_address, amount_eth):
        logger.info(f"[SIMULADO] Comprando {amount_eth} ETH de {token_address}")
        self.simulated_balance["ETH"] -= amount_eth
        self.simulated_balance["TOSHI"] += amount_eth * 1000  # Simulação

    def sell(self, token_address):
        logger.info(f"[SIMULADO] Vendendo TOSHI de {token_address}")
        self.simulated_balance["ETH"] += self.simulated_balance["TOSHI"] / 1000
        self.simulated_balance["TOSHI"] = 0.0
