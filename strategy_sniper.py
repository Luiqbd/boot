import time, math, logging
from web3 import Web3
from config import config
from exchange_client import ExchangeClient
from telegram_alert import TelegramAlert

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sniper")

def amount_out_min(web3, router, amount_in_wei, path, slippage_bps):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
        "outputs": [{"name": "", "type": "uint256[]"}]
    }]
    r = web3.eth.contract(address=router, abi=router_abi)
    out = r.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(web3, router, token, weth):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
        "outputs": [{"name": "", "type": "uint256[]"}]
    }]
    r = web3.eth.contract(address=router, abi=router_abi)
    amt_in = 10**18
    path = [token, weth]
    try:
        out = r.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18
    except:
        return None

def on_new_pair(pair_addr, token0, token1, bot=None):
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    router = Web3.to_checksum_address(config["DEX_ROUTER"])
    alert = TelegramAlert(bot, config["TELEGRAM_CHAT_ID"]) if bot else None

    target_token = token1 if token0.lower() == weth.lower() else token0
    log.info(f"🚀 Novo par aprovado — comprando {target_token}")
    if alert:
        alert.send(f"🚀 Novo par detectado: {target_token}\nPar: {pair_addr}")

    exch = ExchangeClient()
    amt_in = web3.to_wei(config.get("TRADE_SIZE_ETH", 0.02), "ether")
    path_buy = [weth, target_token]
    aout_min = amount_out_min(web3, router, amt_in, path_buy, config["DEFAULT_SLIPPAGE_BPS"])
    deadline = int(time.time()) + config["TX_DEADLINE_SEC"]

    try:
        buy_tx = exch.buy_token(weth, target_token, amt_in)
        log.info(f"✅ Compra enviada — TX: {buy_tx}")
        if alert:
            alert.send(f"✅ Compra realizada: {target_token}\nTX: {buy_tx}")
    except Exception as e:
        log.error(f"❌ Falha na compra: {e}")
        if alert:
            alert.send(f"❌ Falha na compra: {e}")
        return

    entry_price = get_token_price_in_weth(web3, router, target_token, weth)
    if not entry_price:
        log.warning("Não foi possível obter preço inicial")
        if alert:
            alert.send("⚠️ Não foi possível obter preço inicial do token.")
        return

    take_profit_price = entry_price * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0.10)
    highest_price = entry_price
    stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    log.info(f"🎯 TP fixo: {take_profit_price:.6f} WETH | 🛑 SL inicial: {stop_price:.6f} WETH | 📈 Trailing: {trail_pct*100:.1f}%")
    if alert:
        alert.send(f"🎯 TP: {take_profit_price:.6f} WETH\n🛑 SL: {stop_price:.6f} WETH\n📈 Trailing: {trail_pct*100:.1f}%")

    while True:
        current_price = get_token_price_in_weth(web3, router, target_token, weth)
        if not current_price:
            time.sleep(1)
            continue

        if current_price > highest_price:
            highest_price = current_price
            stop_price = highest_price * (1 - trail_pct)
            log.info(f"📈 Novo topo: {highest_price:.6f} WETH | SL ajustado: {stop_price:.6f} WETH")
            if alert:
                alert.send(f"📈 Novo topo: {highest_price:.6f} WETH\n🛑 SL ajustado: {stop_price:.6f} WETH")

        if current_price >= take_profit_price:
            log.info(f"💰 Take-profit atingido ({current_price:.6f} WETH) — vendendo...")
            if alert:
                alert.send(f"💰 Take-profit atingido: {current_price:.6f} WETH — vendendo...")
            break
        if current_price <= stop_price:
            log.info(f"🔻 Stop/trailing atingido ({current_price:.6f} WETH) — vendendo...")
            if alert:
                alert.send(f"🔻 Stop/trailing atingido: {current_price:.6f} WETH — vendendo...")
            break

        time.sleep(2)

    path_sell = [target_token, weth]
    token_contract = web3.eth.contract(address=target_token, abi=[
        {"name": "balanceOf", "type": "function", "stateMutability": "view",
         "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"type": "uint256"}]}
    ])
    balance = token_contract.functions.balanceOf(exch.wallet).call()
    if balance > 0:
        try:
            exch.approve_token(target_token, balance)
            aout_min_sell = amount_out_min(web3, router, balance, path_sell, config["DEFAULT_SLIPPAGE_BPS"])
            sell_tx = exch.sell_token(target_token, weth, balance)
            log.info(f"✅ Venda enviada — TX: {sell_tx}")
            if alert:
                alert.send(f"✅ Venda realizada: {target_token}\nTX: {sell_tx}")
        except Exception as e:
            log.error(f"❌ Falha na venda: {e}")
            if alert:
                alert.send(f"❌ Falha na venda: {e}")
