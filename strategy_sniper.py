import logging
import math
import datetime
import asyncio
from web3 import Web3

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from discovery import is_discovery_running
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager

log = logging.getLogger("sniper")

# ABI mínima para getAmountsOut
ROUTER_ABI = [{
    "name": "getAmountsOut",
    "type": "function",
    "stateMutability": "view",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "", "type": "uint256[]"}]
}]

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(router_contract, amount_in_wei, path, slippage_bps):
    out = router_contract.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(router_contract, token, weth):
    amt_in = 10 ** 18
    path = [token, weth]
    try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def on_new_pair(
    dex_info,
    pair_addr,
    token0,
    token1,
    bot: Bot = None,
    executor=None
):
    log.info(f"🎯 Novo par: {pair_addr} ({token0}/{token1}) na DEX {dex_info['name']}")

    # 1) Conecta na RPC e define WETH
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])

    # 2) Escolhe token alvo
    target_token = token1 if token0.lower() == weth.lower() else token0

    # 3) Parâmetros de trade
    amt_eth        = float(config["TRADE_SIZE_ETH"])
    slippage_bps   = int(config["SLIPPAGE_BPS"])
    stop_loss_pct  = float(config["STOP_LOSS_PCT"]) / 100
    take_profit_pct= float(config["TAKE_PROFIT_PCT"]) / 100
    trail_pct      = stop_loss_pct

    # 4) Preço de entrada
    router = web3.eth.contract(address=dex_info["router"], abi=ROUTER_ABI)
    entry_price = get_token_price_in_weth(router, target_token, weth)
    if not entry_price:
        msg = f"❌ Não foi possível obter preço para {target_token}"
        log.warning(msg)
        TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(msg)
        return

    # 5) Mínimo de saída considerando slippage
    amount_in_wei      = web3.to_wei(amt_eth, "ether")
    amount_out_min_val = amount_out_min(router, amount_in_wei, [weth, target_token], slippage_bps)

    # 6) Safe executor com gestão de risco
    safe_exec = SafeTradeExecutor(
        executor=executor,
        risk_manager=RiskManager()
    )

    # 7) Execução da compra
    buy_tx = safe_exec.buy(
        token_in        = weth,
        token_out       = target_token,
        amount_eth      = amt_eth,
        current_price   = entry_price,
        last_trade_price= None,
        amount_out_min  = amount_out_min_val
    )

    if buy_tx:
        msg = (
            f"🚀 Compra realizada: {target_token}\n"
            f"TX: {buy_tx}\n"
            f"💵 Preço entrada: {entry_price:.6f} WETH"
        )
        log.info(msg)
        TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(msg)
    else:
        warn = f"⚠️ Compra falhou ou bloqueada para {target_token}"
        log.warning(warn)
        TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(warn)
        return

    # 8) Monitoramento de TP/SL
    take_profit_price = entry_price * (1 + take_profit_pct)
    highest_price     = entry_price
    stop_price        = entry_price * (1 - stop_loss_pct)
    sold = False

    log.info(
        f"📈 Monitorando {target_token} para TP {take_profit_price:.6f} / "
        f"SL {stop_price:.6f}"
    )

    while is_discovery_running():
        price = get_token_price_in_weth(router, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        # Ajusta trailing stop
        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        # Condição de saída
        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(
                token_in        = target_token,
                token_out       = weth,
                amount_eth      = amt_eth,
                current_price   = price,
                last_trade_price= entry_price,
                amount_out_min  = None
            )
            if sell_tx:
                msg = (
                    f"💰 Venda realizada: {target_token}\n"
                    f"TX: {sell_tx}\n"
                    f"📊 Preço saída: {price:.6f} WETH"
                )
                log.info(msg)
                TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(msg)
                sold = True
            else:
                warn = f"⚠️ Venda falhou ou bloqueada para {target_token}"
                log.warning(warn)
                TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(warn)
            break

        await asyncio.sleep(3)

    if not sold and not is_discovery_running():
        msg = f"⏹ Monitoramento encerrado para {target_token}."
        log.info(msg)
        TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])._send_sync(msg)
