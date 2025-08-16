import logging
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager
from safe_trade_executor import SafeTradeExecutor

from web3 import Web3

# =========================
# Configurações de Logging
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# =========================
# Configurações do Bot
# =========================
RPC_URL = "https://mainnet.infura.io/v3/SUA_KEY"
PRIVATE_KEY = "SUA_PRIVATE_KEY"

WETH_ADDRESS = Web3.to_checksum_address("0xC02aaa39b223FE8D0A0e5C4F27eAD9083C756Cc2")
TOSHI_ADDRESS = Web3.to_checksum_address("0x...")  # Substitua pelo endereço real

CAPITAL_INICIAL_ETH = 1.0
MAX_EXPOSURE_PCT = 0.1
MAX_TRADES_DIA = 10
LIMITE_PERDAS_SEGUIDAS = 3
TRADE_SIZE_ETH = 0.02

# =========================
# Funções auxiliares
# =========================
def get_price_uniswap_v2(web3, token_in, token_out):
    """
    Lê preço direto de um par Uniswap V2.
    """
    # ABI mínima para Uniswap V2 Pair
    pair_abi = [
        {
            "constant": True,
            "inputs": [],
            "name": "getReserves",
            "outputs": [
                {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
                {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
                {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"}
            ],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "token0",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "token1",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "stateMutability": "view",
            "type": "function"
        }
    ]

    # Endereço do par — você pode obter via subgraph ou hardcodear se já souber
    pair_address = Web3.to_checksum_address("0x...")  # Endereço do par WETH/TOSHI
    pair_contract = web3.eth.contract(address=pair_address, abi=pair_abi)

    token0 = pair_contract.functions.token0().call()
    token1 = pair_contract.functions.token1().call()
    reserves = pair_contract.functions.getReserves().call()

    if token0.lower() == token_in.lower():
        reserve_in, reserve_out = reserves[0], reserves[1]
    else:
        reserve_in, reserve_out = reserves[1], reserves[0]

    price = reserve_out / reserve_in
    return price

# =========================
# Função Principal
# =========================
def main():
    logger.info("🚀 Iniciando estratégia com preço ao vivo...")

    # 1️⃣ Instanciar cliente Web3 + Exchange
    exchange_client = ExchangeClient(rpc_url=RPC_URL, private_key=PRIVATE_KEY)
    web3 = exchange_client.web3

    # 2️⃣ Criar gestor de risco
    risk = RiskManager(
        capital=CAPITAL_INICIAL_ETH,
        max_exposure_pct=MAX_EXPOSURE_PCT,
        max_trades_per_day=MAX_TRADES_DIA,
        loss_limit=LIMITE_PERDAS_SEGUIDAS
    )

    # 3️⃣ Criar executor protegido
    executor = TradeExecutor(exchange_client)
    safe_executor = SafeTradeExecutor(executor, risk)

    # 4️⃣ Obter preços ao vivo
    current_price = get_price_uniswap_v2(web3, WETH_ADDRESS, TOSHI_ADDRESS)
    last_trade_price = current_price * 0.95  # Simulação: último trade foi mais barato

    logger.info(f"💹 Preço atual: {current_price:.8f} | Último: {last_trade_price:.8f}")

    # 5️⃣ Lógica de compra
    if current_price < last_trade_price * 1.05:
        tx = safe_executor.buy(WETH_ADDRESS, TOSHI_ADDRESS, TRADE_SIZE_ETH, current_price, last_trade_price)
        if tx:
            logger.info(f"✅ Compra enviada — TX: {tx}")
        else:
            logger.info("⚠️ Bloqueado pelo RiskManager")
    else:
        logger.info("🕒 Sem entrada agora")

if __name__ == "__main__":
    main()
