import logging
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager
from safe_trade_executor import SafeTradeExecutor
import requests

# =========================
# Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# =========================
# Configurações
# =========================
RPC_URL = "https://mainnet.infura.io/v3/SUA_KEY"
PRIVATE_KEY = "SUA_PRIVATE_KEY"

CAPITAL_INICIAL_ETH = 1.0
MAX_EXPOSURE_PCT = 0.1
MAX_TRADES_DIA = 10
LIMITE_PERDAS_SEGUIDAS = 3
TRADE_SIZE_ETH = 0.02

# =========================
# Funções auxiliares
# =========================
def get_eth_price_usd():
    """Retorna o preço do ETH/USD usando CoinGecko."""
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {"ids": "ethereum", "vs_currencies": "usd"}
    try:
        data = requests.get(url, params=params, timeout=5).json()
        return data["ethereum"]["usd"]
    except Exception as e:
        logger.error(f"Erro ao buscar preço ETH: {e}")
        return None

# =========================
# Função principal
# =========================
def main():
    logger.info("🚀 Bot ETH iniciando...")

    # 1️⃣ Cliente exchange
    exchange_client = ExchangeClient(rpc_url=RPC_URL, private_key=PRIVATE_KEY)

    # 2️⃣ Gestor de risco
    risk = RiskManager(
        capital=CAPITAL_INICIAL_ETH,
        max_exposure_pct=MAX_EXPOSURE_PCT,
        max_trades_per_day=MAX_TRADES_DIA,
        loss_limit=LIMITE_PERDAS_SEGUIDAS
    )

    # 3️⃣ Executor protegido
    executor = TradeExecutor(exchange_client)
    safe_executor = SafeTradeExecutor(executor, risk)

    # 4️⃣ Preço atual ETH/USD
    current_price = get_eth_price_usd()
    if not current_price:
        logger.error("❌ Sem preço do ETH — abortando")
        return

    # Exemplo: Último preço foi armazenado em algum lugar; simulando:
    last_trade_price = current_price * 0.98

    logger.info(f"💹 ETH agora: ${current_price:.2f} | Última operação: ${last_trade_price:.2f}")

    # 5️⃣ Lógica de exemplo
    if current_price < last_trade_price * 1.05:
        tx = safe_executor.buy("ETH", "ETH", TRADE_SIZE_ETH, current_price, last_trade_price)
        if tx:
            logger.info(f"✅ Compra enviada — TX: {tx}")
        else:
            logger.info("⚠️ Bloqueada pelo RiskManager")
    else:
        logger.info("🕒 Sem entrada no momento")

if __name__ == "__main__":
    main()
