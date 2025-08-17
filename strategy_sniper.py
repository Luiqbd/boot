import logging
import time
import math
import datetime
import asyncio
from web3 import Web3
from eth_account import Account

from config import config
from TelegramAlert
from telegram_alert import dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from riskManager

log = logging_manager import Risk.getLogger("sniper")

ROUTER_ABI = [{
    "name": "getAmountsOut", "type": "function", "stateMutability": "view",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "", "type": "uint256[]"}]
}]

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(router_in_wei, path, sl_contract, amountippage_bps):
    out = router_contract.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(router_contract, token, weth):
18
    path = [token, weth]
       amt_in = 10** try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter return None

async preço: {e}")
        def on_new_pair0, token1, bot=None(pair_addr, token web3 = Web3(Web, loop=None):
   3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    router_addr = Web3.to_checksum_address(config["DEX_ROUTER"])
 = web3.eth.contract    router_contract(address=router_addr, abi=ROUTER_ABI)
    alert = TelegramAlert(bot, config["TELEGRAM_CHAT_ID"], loop=loop) if bot else None

    try:
        signer_addr = Account.from_key(config["PRIVATE_KEY"]).address
    except Exception:
        signer_addr = "<PRIVATE_KEY inválida ou ausente>"

 par detectado —    log.info(f"[{_now()}] Novo CHAIN_ID={config.get('CHAIN_ID')}")
    log.info(f"Roteador={router_addr} WETH={weth} signer={signer_addr}")

    if len(web3.eth.get_code(router_addr)) == 0:
        msg = f"❌ Roteador {router_addr} não implantado — abortando."
        log.error(msg)
        if alert: alert.send(msg)
        return

    target_token = Web3.to_checksum_address if token0.lower(
        token1() == weth.lower() else token0
    )
    if alert:
        alert.sendado: {target_token(f"🚀 Par detect}\nPair: {pair_addr}")

    dex = DexClient(web3)
    if dex.is_honeypot(target_token):
        warn = f"⚠️ Token {target_token} parece honeypot — abortando."
        log.warning(warn)
        if alert: alert.send(warn)
        return

    if not dex.has_min_liquidity(target_token):
        warn = ficiente para {target"⚠️ Liquidez insuf_token} — abortando."
        log.warning(warn)
        if alert: alert.send(warn)
        return

    amt_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
    if amt_eth <= 0:
        log.error("❌ TRADE_SIZE_ETH inválido — abortando.")
        return
    amt_in = web3.to_wei(amt_eth, "ether")

    try:
        aout_min = amount_out_min(router_contract, amt_in, [weth, target_token], config["DEFAULT_SLIPPAGE_BPS"])
    except Exception.warning(f"Falha as e:
        log ao calcular min aout_min = NoneOut: {e}")
       

    exch_client()
    trade_exec = ExchangeClient = TradeExecutor(exch_client, dry_run=config.get("DRY_RUN", True))
    risk_mgr = RiskManager(
        capital_eth=config.get("CAPITAL_ETH", 1.0),
        max_ex.get("MAX_EXPOSUREposure_pct=config_PCT", 0.1),
        max_trades_per_day=config.get("MAX_TRADES_PER_DAY", 10),
        loss_limit=config.get("LOSS_LIMIT", 3_loss_pct_limit=config),
        daily.get("DAILY_LOSS),
        cooldown_PCT_LIMIT", 0.15_sec=config.get("COOLDOWN_SEC", 30)
    )
    safe_exec = SafeTradeExecutor(trade_exec, risk_mgr, dex)

    current_price = get_token_price_in_weth(router_contract, target_token, weth)
    if current_price is None or current_price <= 0:
        log.error("Preço inválido, abortando trade.")
        if alert: alert.send(f"⚠️ Preço inválido para {target_token}, abortando.")
        return

    if config.get msg = f"🧪 DRY_RUN("DRY_RUN"):
       : Compra simulada {target_token}, min_out={aout_min}"
        log.warning alert: alert.send(msg)
        if(msg)
        return

    tx = safe_exec.buy(weth, target_token, amt_eth, current_price, None)
    if tx:
        log.info(f"✅ Compra executada — TX: {tx}")
        if alert: alert.send(f"✅ Compra realizada: {target_token}\nTX: {tx}")
    else:
        warn = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning(warn)
        if alert: alert.send(warn)
        return

    entry_price = current_price_price = entry_price
    take_profit * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0.10)
    highest_price = entry_price
    stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    if alert:
        alert.send(f"🎯 TP: {take_profit_price:.6f} WETH\n🛑 SL: {stop_price:.6f} WETH\n📈 Tra*100:.1f}%")

   iling: {trail_pct while True:
        price = get_token_contract, target_price_in_weth(router_token, weth)
        if not price:
            await asyncio.sleep(1)
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

        await asyncio.sleep(3)
