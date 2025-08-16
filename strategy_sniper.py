import time, math
import logging
from web3 import Web3
from config import config
from discovery import run_discovery
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager
from safe_trade_executor import SafeTradeExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sniper")

def amount_out_min(web3, router, amount_in_wei, path, slippage_bps):
    router_abi = [
        {
            "name":"getAmountsOut",
            "type":"function",
            "stateMutability":"view",
            "inputs":[{"name":"amountIn","type":"uint256"},{"name":"path","type":"address[]"}],
            "outputs":[{"name":"","type":"uint256[]"}]
        }
    ]
    r = web3.eth.contract(address=router, abi=router_abi)
    out = r.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def on_new_pair(pair_addr, token0, token1):
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    router = Web3.to_checksum_address(config["DEX_ROUTER"])

    # Qual token é o alvo (não-WETH)
    target_token = token1 if token0.lower() == weth.lower() else token0
    log.info(f"🚀 Preparando compra do token {target_token} no par {pair_addr}")

    # Instancia cliente/executor/risco
    exch = ExchangeClient(config["RPC_URL"], config["PRIVATE_KEY"])
    risk = RiskManager(
        capital=1.0,
        max_exposure_pct=config.get("MAX_EXPOSURE_PCT", 0.10),
        max_trades_per_day=config.get("MAX_TRADES_DIA", 20),
        loss_limit=config.get("LOSS_STREAK_LIMIT", 3)
    )
    executor = TradeExecutor(exch)
    safe_exec = SafeTradeExecutor(executor, risk)

    # Parâmetros de compra
    amt_in = web3.to_wei(config.get("TRADE_SIZE_ETH", 0.02), "ether")
    path_buy = [weth, target_token]
    aout_min = amount_out_min(web3, router, amt_in, path_buy, config["DEFAULT_SLIPPAGE_BPS"])
    deadline = int(time.time()) + config["TX_DEADLINE_SEC"]

    # Execução direta usando ExchangeClient (para velocidade máxima)
    try:
        txh = exch.buy_v2(router, amt_in, aout_min, path_buy, deadline, tip_gwei=config.get("TIP_GWEI", 5), max_multiplier=2)
        log.info(f"✅ Compra enviada. TX hash: {txh}")
    except Exception as e:
        log.error(f"❌ Falha na compra: {e}")

if __name__ == "__main__":
    run_discovery(on_new_pair)
