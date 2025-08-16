import logging
import json
import os
from datetime import datetime
from web3 import Web3
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager
from safe_trade_executor import SafeTradeExecutor

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

# Endereços e pool para preço ETH/USDC Uniswap V3
WETH_ADDRESS = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
USDC_ADDRESS = Web3.to_checksum_address("0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
POOL_ADDRESS = Web3.to_checksum_address("0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8")

LAST_PRICE_FILE = "last_price.json"
TRADES_LOG_FILE = "trades.jsonl"

# =========================
# Funções auxiliares
# =========================
def get_eth_price_uniswap_v3(web3):
    pool_abi = [{
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }]
    pool = web3.eth.contract(address=POOL_ADDRESS, abi=pool_abi)
    sqrtPriceX96 = pool.functions.slot0().call()[0]
    price = (sqrtPriceX96 ** 2) / (2 ** 192)
    return 1 / price  # ETH/USD

def load_last_price():
    if os.path.exists(LAST_PRICE_FILE):
        try:
            with open(LAST_PRICE_FILE, "r") as f:
                return json.load(f).get("last_price")
        except:
            return None
    return None

def save_last_price(price):
    with open(LAST_PRICE_FILE, "w") as f:
        json.dump({"last_price": price}, f)

def log_trade(trade_type, price, amount_eth, tx_hash, success=True):
    trade_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "type": trade_type,
        "price_usd": price,
        "amount_eth": amount_eth,
        "tx_hash": tx_hash,
        "success": success
    }
    with open(TRADES_LOG_FILE, "a") as f:
        f.write(json.dumps(trade_entry) + "\n")
    logger.info(f"📝 Trade registrado: {trade_entry}")

# =========================
# Função principal
# =========================
def main():
    logger.info("🚀 Bot ETH On-chain com log de trades iniciado...")

    # Conexão Web3 + ExchangeClient
    exchange_client = ExchangeClient(rpc_url=RPC_URL, private_key=PRIVATE_KEY)
    web3 = exchange_client.web3

    # Gestor de risco
    risk = RiskManager(
        capital=CAPITAL_INICIAL_ETH,
        max_exposure_pct=MAX_EXPOSURE_PCT,
        max_trades_per_day=MAX_TRADES_DIA,
        loss_limit=LIMITE_PERDAS_SEGUIDAS
    )

    # Executor protegido
    executor = TradeExecutor(exchange_client)
    safe_executor = SafeTradeExecutor(executor, risk)

    # Preço atual e último preço
    current_price = get_eth_price_uniswap_v3(web3)
    last_trade_price = load_last_price()
    if last_trade_price is None:
        last_trade_price = current_price
        logger.info("📂 Nenhum histórico encontrado — usando preço atual como referência inicial.")

    logger.info(f"💹 ETH agora: ${current_price:.2f} | Última operação: ${last_trade_price:.2f}")

    # Lógica de compra
    if current_price < last_trade_price * 1.05:
        tx = safe_executor.buy("ETH", "ETH", TRADE_SIZE_ETH, current_price, last_trade_price)
        if tx:
            logger.info(f"✅ Compra executada — TX: {tx}")
            save_last_price(current_price)
            log_trade("buy", current_price, TRADE_SIZE_ETH, tx, success=True)
        else:
            logger.info("⚠️ Compra bloqueada pelo RiskManager")
            log_trade("buy", current_price, TRADE_SIZE_ETH, None, success=False)
    else:
        logger.info("🕒 Sem entrada no momento")

if __name__ == "__main__":
    main()
