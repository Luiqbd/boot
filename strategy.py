import logging
from risk_manager import RiskManager  # Certifique-se de importar corretamente

logger = logging.getLogger(__name__)

class TradingStrategy:
    def __init__(self, dex_client, trader, alert, capital=1.0):
        self.dex = dex_client
        self.trader = trader
        self.alert = alert
        self.last_price = None
        self.token_address = "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4"
        self.risk = RiskManager(capital=capital)

    def run(self):
        price = self.dex.get_token_price(self.token_address)

        if price is None:
            logger.warning("Preço não disponível")
            return

        logger.info(f"Preço atual do token: {price:.6f}")

        if self.last_price is None:
            self.last_price = price
            self.alert.send(f"📊 Monitorando TOSHI — Preço inicial: {price:.6f}")
            return

        change = (price - self.last_price) / self.last_price

        # Verifica se pode comprar
        if change <= -0.05:
            if self.risk.can_trade(price, self.last_price, "buy"):
                self.trader.buy(self.token_address, amount_eth=0.01)
                self.alert.send(f"📉 TOSHI caiu {change*100:.2f}% — Simulando COMPRA de 0.01 ETH")
                self.risk.register_trade(success=True)
            else:
                self.alert.send("🚫 Compra bloqueada pelo gestor de risco")

        # Verifica se pode vender
        elif change >= 0.05:
            if self.risk.can_trade(price, self.last_price, "sell"):
                self.trader.sell(self.token_address)
                self.alert.send(f"📈 TOSHI subiu {change*100:.2f}% — Simulando VENDA total")
                self.risk.register_trade(success=True)
            else:
                self.alert.send("🚫 Venda bloqueada pelo gestor de risco")

        self.last_price = price
