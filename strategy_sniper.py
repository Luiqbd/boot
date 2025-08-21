import logging
import math
import datetime
import asyncio
from web3 import Web3

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient

log = logging.getLogger("sniper")

ROUTER_ABI = [{
    "name": "getAmountsOut",
    "type": "function",
    "stateMutability": "view",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path",     "type": "address[]"}
    ],
    "outputs": [{"name": "", "type": "uint256[]"}]
}]

def nowiso():
    """Retorna a hora atual em ISO8601 (segundos)."""
    return datetime.datetime.now().isoformat(timespec="seconds")

def amountoutmin(router, amtinwei, path, slippage_bps):
    """Calcula o amountOutMin com base no slippage."""
    out = router.functions.getAmountsOut(amtinwei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10000))

def getpriceweth(router, token, weth):
    """Retorna o preço em WETH por 1 unidade do token."""
    try:
        out = router.functions.getAmountsOut(10**18, [token, weth]).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def onnewpair(
    dex_info,
    pair_addr,
    token0,
    token1,
    bot=None,
    loop=None,
    executor=None
):
    """Callback chamado quando um novo par surge."""
    if executor is None:
        raise RuntimeError("Executor não passado em onnewpair()")

    # 🔹 Cria alerta acumulativo
    alert = TelegramAlert(bot, chatid=int(config["TELEGRAMCHAT_ID"]))

    dex = DexClient(dex_info)
    w3 = executor.w3
    router = w3.eth.contract(address=dex.router, abi=ROUTER_ABI)
    exchange = ExchangeClient(w3, router)
    executor.setexchangeclient(exchange)

    if dex.is_weth(token1):
        target, weth = token0, token1
    else:
        target, weth = token1, token0

    amteth = executor.tradesize
    entryprice = getpriceweth(router, target, weth)
    if entryprice is None or entryprice <= 0:
        alert.log_event(f"⚠️ Preço de entrada inválido para {target}. Abortando.")
        alert.flush_report()
        return

    buyamountinwei = int(amteth * 1e18)
    minoutbuy = amountoutmin(router, buyamountinwei, [weth, target], executor.slippage_bps)

    # 1️⃣ Compra
    buy_tx = await executor.buy(
        path=[weth, target],
        amountinwei=buyamountinwei,
        amountoutmin=minoutbuy
    )
    if not buy_tx:
        alert.log_event(f"⚠️ Compra não realizada: {target}")
        alert.flush_report()
        return

    alert.log_event(f"🛒 Compra confirmada: {target}\nTX: {buy_tx}")

    # 2️⃣ Monitoramento para venda
    highest = entryprice
    trailpct = executor.trailpct / 100
    tpprice = entryprice * (1 + executor.takeprofitpct / 100)
    stoppx = highest * (1 - trailpct)

    from discovery import isdiscoveryrunning
    sold = False
    finalprice = entryprice

    while isdiscoveryrunning():
        price = getpriceweth(router, target, weth)
        if price is None:
            await asyncio.sleep(1)
            continue

        finalprice = price

        if price > highest:
            highest = price
            stoppx = highest * (1 - trailpct)

        if price >= tpprice or price <= stoppx:
            sellamountinwei = int(amteth * 1e18)
            minoutsell_weth = math.floor(price * 1e18 * (1 - executor.slippage_bps / 10000))

            sell_tx = await executor.sell(
                path=[target, weth],
                amountinwei=sellamountinwei,
                minout=minoutsell_weth
            )
            if sell_tx:
                alert.log_event(f"💰 Venda realizada: {target}\nTX: {sell_tx}")
                sold = True
            else:
                alert.log_event(f"⚠️ Venda bloqueada: {target}")
            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    # 3️⃣ Finalização
    if not sold:
        alert.log_event(f"⏹ Monitoramento finalizado para {target} (sniper parado).")

    if getattr(executor, "dry_run", False):
        if not hasattr(executor, "simulated_pnl"):
            executor.simulated_pnl = 0.0
        if entryprice and finalprice:
            pnleth = amteth * (finalprice / entryprice - 1.0)
            executor.simulated_pnl += pnleth
            alert.log_event(f"[SIMULADO] PnL desta operação: {pnleth:+.6f} ETH | "
                            f"Acumulado: {executor.simulated_pnl:+.6f} ETH")

    # 🔹 Envia tudo junto
    alert.flush_report()
