from flask import Flask, render_template_string
from strategy import TradingStrategy
from dex import DexClient
from paper_trader import PaperTrader
from telegram_alert import TelegramAlert

app = Flask(__name__)

# Instâncias simuladas (substitua pelas reais)
dex = DexClient()
trader = PaperTrader()
alert = TelegramAlert()
strategy = TradingStrategy(dex, trader, alert)

# Template HTML simples
TEMPLATE = """
<!doctype html>
<title>Painel de Monitoramento</title>
<h1>📊 Painel TOSHI</h1>
<ul>
  <li>Preço atual: {{ price }}</li>
  <li>Variação: {{ change }}%</li>
  <li>Trades hoje: {{ trades }}</li>
  <li>Streak de perdas: {{ losses }}</li>
</ul>
"""

@app.route("/")
def dashboard():
    price = dex.get_token_price(strategy.token_address)
    last = strategy.last_price or price
    change = ((price - last) / last * 100) if last else 0
    trades = strategy.risk.daily_trades
    losses = strategy.risk.loss_streak

    return render_template_string(TEMPLATE,
                                  price=f"{price:.6f}",
                                  change=f"{change:.2f}",
                                  trades=trades,
                                  losses=losses)

if __name__ == "__main__":
    app.run(debug=True)
