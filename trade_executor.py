import time
import logging

logger = logging.getLogger(__name__)

class TradeExecutor:
    def __init__(self, exchange_client):
        self.client = exchange_client
        self.executed_orders = set()

    def buy(self, token_in, token_out, amount_eth):
        order_id = f"buy-{token_out}-{int(time.time())}"
        if order_id in self.executed_orders:
            logger.warning("⚠️ Ordem de compra duplicada detectada — ignorando")
            return None

        amount_wei = self.client.web3.toWei(amount_eth, "ether")
        try:
            tx_hash = self.client.buy_token(token_in, token_out, amount_wei)
            self.executed_orders.add(order_id)
            logger.info(f"✅ Compra executada — TX: {tx_hash}")
            return tx_hash
        except Exception as e:
            logger.error(f"❌ Erro ao executar compra: {e}")
            return None

    def sell(self, token_in, token_out, amount_eth):
        order_id = f"sell-{token_in}-{int(time.time())}"
        if order_id in self.executed_orders:
            logger.warning("⚠️ Ordem de venda duplicada detectada — ignorando")
            return None

        amount_wei = self.client.web3.toWei(amount_eth, "ether")
        try:
            tx_hash = self.client.sell_token(token_in, token_out, amount_wei)
            self.executed_orders.add(order_id)
            logger.info(f"✅ Venda executada — TX: {tx_hash}")
            return tx_hash
        except Exception as e:
            logger.error(f"❌ Erro ao executar venda: {e}")
            return None
