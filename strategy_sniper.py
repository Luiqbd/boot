import time, math, logging, datetime
from web3 import Web3
from eth_account import Account
from config import config
from telegram_alert import TelegramAlert
from dex import DexClient

# Camada segura de execução
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("sniper")

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(web3, router, amount_in_wei, path, slippage_bps):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"}
        ],
        "outputs": [{"name": "", "type": "uint256[]"}]
    }]
    r = web3.eth.contract(address=router, abi=router_abi)
    out = r.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(web3, router, token, weth):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"}
        ],
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

    try:
        signer_addr = Account.from_key(config["PRIVATE_KEY"]).address
    except Exception:
        signer_addr = "<PRIVATE_KEY inválida ou ausente>"

    log.info(f"[{_now()}] Novo par detectado — CHAIN_ID={config.get('CHAIN_ID')}")
    log.info(f"[{_now()}] Roteador={router} WETH={weth} DRY_RUN={config.get('DRY_RUN')}")
    log.info(f"[{_now()}] signer={signer_addr} TRADE_SIZE_ETH={config.get('TRADE_SIZE_ETH', 0.02)}")

    # Checa se roteador está implantado
    if len(web3.eth.get_code(router)) == 0:
        msg = f"❌ Roteador {router} não implantado — abortando."
        log.error(msg)
        if alert: alert.send(msg)
        return

    # Define token alvo
    target_token = Web3.to_checksum_address(
        token1 if token0.lower() == weth.lower() else token0
    )
    if alert:
        alert.send(f"🚀 Par detectado: {target_token}\nPair: {pair_addr}")

    # Checagens on-chain
    dex = DexClient(web3)
    if dex.is_honeypot(target_token):
        warn = f"⚠️ Token {target_token} parece honeypot — abortando."
        log.warning(warn)
        if alert: alert.send(warn)
        return

    if not dex.has_min_liquidity(target_token):
        warn = f"⚠️ Liquidez insuficiente para {target_token} — abortando."
        log.warning(warn)
        if alert: alert.send(warn)
        return

    amt_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
    if amt_eth <= 0:
        log.error("❌ TRADE_SIZE_ETH inválido — abortando.")
        return
    amt_in = web3.to_wei(amt_eth, "ether")

    # Calcula minOut
    try:
        aout_min = amount_out_min(web3, router, amt_in, [weth, target_token], config["DEFAULT_SLIPPAGE_BPS"])
    except Exception as e:
        log.warning(f"Falha ao calcular minOut: {e}")
        aout_min = None

    # Instancia execução segura
    exch_client = ExchangeClient()
    trade_exec = TradeExecutor(exch_client, dry_run=config.get("DRY_RUN", True))
    risk_mgr = RiskManager(
        capital_eth=1.0,
        max_exposure_pct=0.1,
        max_trades_per_day=10,
        loss_limit=3,
        daily_loss_pct_limit=0.15,
        cooldown_sec=30
    )
    safe_exec = SafeTradeExecutor(trade_exec, risk_mgr, dex)

    current_price = get_token_price_in_weth(web3, router, target_token, weth)
    last_trade_price = None

    # DRY_RUN
    if config.get("DRY_RUN"):
        msg = f"🧪 DRY_RUN: Compra simulada {target_token}, min_out={aout_min}"
        log.warning(msg)
        if alert: alert.send(msg)
        return

    # Compra segura
    tx = safe_exec.buy(weth, target_token, amt_eth, current_price, last_trade_price)
    if tx:
        log.info(f"✅ Compra executada — TX: {tx}")
        if alert: alert.send(f"✅ Compra realizada: {target_token}\nTX: {tx}")
    else:
        warn = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning(warn)
        if alert: alert.send(warn)
        return

    # Configura TP/SL
    entry_price = current_price
    take_profit_price = entry_price * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0.10)
    highest_price = entry_price
    stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    if alert:
        alert.send(f"🎯 TP: {take_profit_price:.6f} WETH\n🛑 SL: {stop_price:.6f} WETH\n📈 Trailing: {trail_pct*100:.1f}%")

    # Loop de monitoramento para venda segura
    while True:
        price = get_token_price_in_weth(web3, router, target_token, weth)
        if not price:
            time.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(target_token, weth, amt_eth, price, entry_price)
            if sell_tx:
                log.info(f"💰 Venda executada — TX: {sell_tx}")
                if alert: alert.send(f"💰 Venda realizada: {target_token}\nTX: {sell_tx}")
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                if alert: alert.send(warn)
            break

        time.sleep(3)
