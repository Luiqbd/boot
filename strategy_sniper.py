import logging
import math
import datetime
import asyncio
from web3 import Web3

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from risk_manager import RiskManager

log = logging.getLogger("sniper")

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
    bot=None,
    loop=None,
    executor: TradeExecutor = None
):
    """
    Callback disparado pelo discovery quando encontra um novo pair.
    executor: instância de SafeTradeExecutor ou RealTradeExecutor,
    criada lá no main.py e injetada aqui.
    """
    # checa executor
    if executor is None:
        raise RuntimeError("Trade executor não foi passado para on_new_pair()")

    # monta notificação assíncrona + risk manager
    alert = TelegramAlert(bot, chat_id=int(config["TELEGRAM_CHAT_ID"]))
    risk_manager = RiskManager(
        max_trade_size=executor.trade_size,
        slippage_bps=executor.slippage_bps
    )

    # prepara cliente DEX e contrato
    dex = DexClient(dex_info)
    w3 = executor.w3
    router = w3.eth.contract(address=dex.router, abi=ROUTER_ABI)
    exchange = ExchangeClient(w3, router)

    # define token alvo e WETH
    if dex.is_weth(token1):
        target_token, weth = token0, token1
    else:
        target_token, weth = (token1, token0)

    # calcula valores de trade
    amt_eth = executor.trade_size
    entry_price = get_token_price_in_weth(router, target_token, weth)
    min_out = amount_out_min(
        router,
        int(amt_eth * 1e18),
        [weth, target_token],
        executor.slippage_bps
    )

    # 1) faz a compra
    buy_tx = await executor.buy(
        path=[weth, target_token],
        amount_in_wei=int(amt_eth * 1e18),
        amount_out_min=min_out
    )
    if not buy_tx:
        msg = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning(msg)
        alert.send(msg)
        return

    msg = f"🛒 Compra confirmada\nToken: {target_token}\nTX: {buy_tx}"
    log.info(msg)
    alert.send(msg)

    # 2) monitora para vender (trail + take profit)
    highest_price = entry_price
    trail_pct = executor.trail_pct / 100
    take_profit_price = entry_price * (1 + executor.take_profit_pct / 100)
    stop_price = highest_price * (1 - trail_pct)

    from discovery import is_discovery_running
    sold = False

    while is_discovery_running():
        price = get_token_price_in_weth(router, target_token, weth)
        if price is None:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = await executor.sell(
                path=[target_token, weth],
                amount_in_wei=int(amt_eth * 1e18),
                min_out=math.floor(price * 1e18 * (1 - executor.slippage_bps / 10_000))
            )
            if sell_tx:
                msg = f"💰 Venda executada\nToken: {target_token}\nTX: {sell_tx}"
                log.info(msg)
                alert.send(msg)
                sold = True
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                alert.send(warn)
            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    # se saiu do loop sem vender (sniper parado)
    if not sold:
        msg = f"⏹ Monitoramento encerrado para {target_token} (sniper parado)."
        log.info(msg)
        alert.send(msg)
