class TradingStrategy:
    def __init__(self, dex):
        self.dex = dex

    def run(self):
        price = self.dex.get_price()
        print(f"Preço atual: {price}")

        if self.should_buy(price):
            self.dex.buy()
        elif self.should_sell(price):
            self.dex.sell()

    def should_buy(self, price):
        return price < 1.0  # Exemplo simples

    def should_sell(self, price):
        return price > 1.5