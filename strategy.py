import logging

logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self, dex_client, trader, alert):
        self.dex = dex_client
        self.trader = trader
        self.alert = alert
        self.last_price = None

    def run(self):
        token_address = "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4"  # TOSHI
        price = self.dex.get_token_price(token_address)

        if price is None:
            logger.warning("Preço não disponível")
            return

        logger.info(f"Preço atual do token: {price:.6f}")

        if self.last_price is None:
            self.last_price = price
            return

        # Lógica simples: compra se caiu >5%, vende se subiu >5%
        change = (price - self.last_price) / self.last_price

        if change <= -0.05:
            self.trader.buy(token_address, amount_eth=0.01)
            self.alert.send(f"📉 Token caiu {change*100:.2f}% — Simulando COMPRA")
        elif change >= 0.05:
            self.trader.sell(token_address)
            self.alert.send(f"📈 Token subiu {change*100:.2f}% — Simulando VENDA")

        self.last_price = price
