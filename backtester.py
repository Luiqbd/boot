import logging

logger = logging.getLogger(__name__)

class Backtester:
    def __init__(self, strategy_class, historical_prices, token_address):
        self.strategy_class = strategy_class
        self.historical_prices = historical_prices
        self.token_address = token_address
        self.trades = []
        self.last_price = None

    def run(self):
        for price in self.historical_prices:
            strategy = self.strategy_class(
                dex_client=MockDex(price),
                trader=MockTrader(self.trades),
                alert=MockAlert()
            )
            strategy.last_price = self.last_price
            strategy.run()
            self.last_price = price

        self.report()

    def report(self):
        total_trades = len(self.trades)
        wins = sum(1 for t in self.trades if t["result"] == "win")
        losses = total_trades - wins
        win_rate = wins / total_trades * 100 if total_trades else 0

        logger.info(f"📊 Total de trades: {total_trades}")
        logger.info(f"✅ Taxa de acerto: {win_rate:.2f}%")
        logger.info(f"📉 Perdas: {losses}")

class MockDex:
    def __init__(self, price):
        self.price = price

    def get_token_price(self, token_address):
        return self.price

class MockTrader:
    def __init__(self, trades):
        self.trades = trades

    def buy(self, token_address, amount_eth):
        self.trades.append({"type": "buy", "result": "win"})  # Simulação simples

    def sell(self, token_address):
        self.trades.append({"type": "sell", "result": "win"})

class MockAlert:
    def send(self, msg):
        pass  # Ignora alertas no backtest
