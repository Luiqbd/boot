import logging
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager
from safe_trade_executor import SafeTradeExecutor  # Wrapper que criamos

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Endereços de exemplo
WETH_ADDRESS = "0x..."
TOSHI_ADDRESS = "0x..."

def main():
    # 1️⃣ Configurar cliente de exchange
    exchange_client = ExchangeClient(
        rpc_url="https://mainnet.infura.io/v3/SUA_KEY",
        private_key="SUA_PRIVATE_KEY"
    )

    # 2️⃣ Criar gestor de risco
    risk = RiskManager(
        capital=1.0,
        max_exposure_pct=0.1,
        max_trades_per_day=10,
        loss_limit=3
    )

    # 3️⃣ Criar executor protegido
    executor = TradeExecutor(exchange_client)
    safe_executor = SafeTradeExecutor(executor, risk)

    # 4️⃣ Lógica simplificada de estratégia
    last_trade_price = 0.0022
    current_price = 0.0025
    trade_size_eth = 0.02

    # Exemplo: Comprar se preço caiu pouco desde último trade
    if current_price < last_trade_price * 1.05:
        tx = safe_executor.buy(WETH_ADDRESS, TOSHI_ADDRESS, trade_size_eth, current_price, last_trade_price)
        if tx:
            logger.info(f"📈 Compra enviada: {tx}")
        else:
            logger.info("⚠️ Compra bloqueada pelo RiskManager")
    else:
        logger.info("🕒 Sem entrada no momento — aguardando oportunidade")

if __name__ == "__main__":
    main()
