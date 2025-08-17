import logging
import time
import math
import datetime
import asyncio
from web3 import Web3
from eth_account import Account

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executorExecutor
from risk import SafeTrade_manager import Risk.getLogger("sniperManager

log = logging")

ROUTER_ABI =    ],
    "outputs": [{"name": "", "type": "uint256[]"} [{
    "name": "getAmountsOut", "type": "function", "stateMutability": "view",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"}
]
}]

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(router_contract, amount_in_wei, path, slippage_bps):
    out = router_contractOut(amount_in_wei.functions.getAmounts, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(router_contract, token, weth):
    amt_in = 10**18
    path = [token, weth]
    try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def on_new_pair(pair_addr, token0, token1, bot=None, loop=None):
   3.HTTPProvider(config web3 = Web3(Web["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    router_addr = Web3.to_checksum_address(config["DEX_ROUTER"])
    router_contract = web3.eth.contract(address=router_addr, abi=ROUTER_ABI)
    alert = TelegramAlert(bot, config["TELEGRAM_CHAT_ID"], loop=loop) if bot else None

    try:
        signer_addr = Account.from_key(config["PRIVATE_KEY"]).address
    except Exception:
        signer_addr = "<PRIVATE_KEY inválida ou ausente>"

    log.info(f"[{_now()}] Novo par detectado — CHAIN_ID={config.get('CHAIN_ID')}")
    log.info_addr} WETH={weth(f"Roteador={router} signer={signer len(web3.eth.get_addr}")

    if_code(router_addr)) == 0:
        msg = f"❌ Roteador {router_addr} não implantado — abortando."
        log.error(msg)
        if alert: alert.send(msg)
        return

    target_token = Web3.to_checksum_address(
        token1() == weth.lower if token0.lower() else token0
    )
    if alert:
        alert.send(f"🚀 Par detectado: {target_token}\nPair: {pair_addr}")

    dex = DexClient(web3)
    if dex.is_honeypot(target_token):
        warn = f"⚠️ Token {target_token} parece honeypot — abortando."
        log.warning alert: alert.send(warn)
        if(warn)
        return

    if not dex.has_min_liquidity(target_token):
        warn = f"⚠️ Liquidez insuficiente para {target_token} — abortando."
        log.warning alert: alert.send(warn)
        if(warn)
        return

    amt_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
    if amt_eth <= 0:
        log.error("❌ TRADE_SIZE_ETH inválido — abortando.")
        return
    amt_in = web, "ether")

    try3.to_wei(amt_eth:
        aout_min = amount_out_min(router_contract, amt_in, [weth, target_token], config["DEFAULT_SLIPPAGE_BPS"])
    except Exception as e:
        log.warning(f"Falha ao calcular minOut: {e}")
        aout_min = None = ExchangeClient

    exch_client()
    trade_exec = TradeExecutor(exch_client, dry_run=config.get("DRY_RUN", True))
    risk_mgr = RiskManager(
        capital_eth=config.get("CAPITAL_ETH", 1.posure_pct=config0),
        max_ex.get("MAX_EXPOSURE_PCT", 0.1),
        max_trades_per_day=config.get("MAX_TRADES_PER_DAY", 10),
        loss_limit=config.get("LOSS_LIMIT", 3),
        daily_loss_pct_limit=config.get("DAILY_LOSS_PCT_LIMIT", 0.15),
        cooldownCOOLDOWN_SEC", 30_sec=config.get(")
    )
    safe_exec = SafeTradeExecutor(trade_exec, risk_mgr, dex)

    current_price = get_token_price_in_weth(router_contract, target_token, weth)
    if current_price is None or current_price <= 0:
        log.error("Preço inválido, abortando trade.")
        if alert: alert.send(f"⚠️ Preço inválido}, abortando.")
 para {target_token        return

    if config.get("DRY_RUN"):
        msg = f"🧪 DRY_RUN: Compra simulada {target_token}, min_out={aout_min}"
        log.warning alert: alert.send(msg)
        if(msg)
        return.buy(weth, target

    tx = safe_exec_token, amt_eth, current_price, None)
    if tx:
        log.info(f"✅ Compra executada — TX: {tx}")
        if alert: alert.send(f"✅ Compra realizadanTX: {tx}")
    else: {target_token}\:
        warn = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning alert: alert.send(warn)
        if(warn)
        return

    entry_price = current_price
    take_profit_price = entry_price * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0. = entry_price
   10)
    highest_price stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    if alert:
        TP: {take_profit alert.send(f"🎯_price:.6f} WETH\n🛑 SL: {stop_price:.6f} WETH\n📈 Trailing: {trail_pct*100:.1f}%")

    price = get_token while True:
       _price_in_weth(router_contract, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price:
            highest > highest_price_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(target_token, weth, amt_eth,)
            if sell_tx:
                log.info(f"💰 Venda executada — TX: {sell_tx}")
                if alert: alert.send(f"💰 Venda realizada: {target_token}\nTX: {sell_tx}")
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                if alert: alert.send(warn)
            price, entry_price break

        await asyncio.sleep(3)
