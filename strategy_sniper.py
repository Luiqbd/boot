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
    return datetime.datetime.now().isoformat(timespec="seconds")

def amountoutmin(router, amtinwei, path, slippage_bps):
    out = router.functions.getAmountsOut(amtinwei, path).call()[-1]
    return math.floor(out * (1 - slippagebps / 10000))

def getpriceweth(router, token, weth):
    """
    Retorna o preço em WETH por 1 unidade do token (ETH-per-token).
    """
    try:
        out = router.functions.getAmountsOut(1018, [token, weth]).call()[-1]
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
    """
    Callback do discovery.py quando um novo par surge.
    executor: instância de TradeExecutor (real ou dry-run).
    """
    if executor is None:
        raise RuntimeError("Executor não passado em onnewpair()")

    # prepara notificação
    alert = TelegramAlert(bot, chatid=int(config["TELEGRAMCHAT_ID"]))

    # monta cliente DEX e injeta no executor
    dex = DexClient(dex_info)
    w3 = executor.w3
    router = w3.eth.contract(address=dex.router, abi=ROUTER_ABI)
    exchange = ExchangeClient(w3, router)
    executor.setexchangeclient(exchange)

    # define token alvo e WETH
    if dex.is_weth(token1):
        target, weth = token0, token1
    else:
        target, weth = token1, token0

    # valores de trade
    amteth = executor.tradesize
    entryprice = getprice_weth(router, target, weth)
    if entryprice is None or entryprice <= 0:
        msg = f"⚠️ Preço de entrada inválido para {target}. Abortando."
        log.warning(msg)
        alert.send(msg)
        return

    # minout para a compra (WETH -> TOKEN), baseado no tradesize
    buyamountinwei = int(amteth * 1e18)
    minoutbuy = amountoutmin(
        router,
        buyamountin_wei,
        [weth, target],
        executor.slippage_bps
    )

    # 1) executa compra
    buy_tx = await executor.buy(
        path=[weth, target],
        amountinwei=buyamountin_wei,
        amountoutmin=minoutbuy
    )
    if not buy_tx:
        msg = f"⚠️ Compra não realizada: {target}"
        log.warning(msg)
        alert.send(msg)
        return

    msg = f"🛒 Compra confirmada: {target}\nTX: {buy_tx}"
    log.info(msg)
    alert.send(msg)

    # 2) monitora e faz venda (trail + take profit)
    highest = entry_price
    trailpct = executor.trailpct / 100
    tpprice  = entryprice * (1 + executor.takeprofitpct / 100)
    stoppx   = highest * (1 - trailpct)

    from discovery import isdiscoveryrunning
    sold = False
    finalprice = entryprice  # último preço conhecido (ou de venda)

    while isdiscoveryrunning():
        price = getpriceweth(router, target, weth)
        if price is None:
            await asyncio.sleep(1)
            continue

        # atualiza referência para PnL parcial caso pare sem vender
        final_price = price

        # trailing stop dinâmico
        if price > highest:
            highest = price
            stoppx = highest * (1 - trailpct)

        # take profit ou stop acionado
        if price >= tpprice or price <= stoppx:
            # min_out da venda (TOKEN -> WETH):
            # mantém consistência com a sua assinatura do executor
            sellamountinwei = int(amteth * 1e18)  # segue seu padrão atual
            minoutsell_weth = math.floor(
                price  1e18  (1 - executor.slippagebps / 10000)
            )

            sell_tx = await executor.sell(
                path=[target, weth],
                amountinwei=sellamountin_wei,
                minout=minoutsellweth
            )
            if sell_tx:
                msg = f"💰 Venda realizada: {target}\nTX: {sell_tx}"
                log.info(msg)
                alert.send(msg)
                sold = True
            else:
                msg = f"⚠️ Venda bloqueada: {target}"
                log.warning(msg)
                alert.send(msg)
            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    # se parou sem vender
    if not sold:
        msg = f"⏹ Monitoramento finalizado para {target} (sniper parado)."
        log.info(msg)
        alert.send(msg)

    # 3) atualiza PnL simulado se estiver em dry_run
    # Fórmula correta em ETH: PnL = amteth * (finalprice / entry_price - 1)
    if getattr(executor, "dry_run", False):
        if not hasattr(executor, "simulated_pnl"):
            executor.simulated_pnl = 0.0
        if entryprice and finalprice:
            pnleth = amteth * (finalprice / entryprice - 1.0)
            executor.simulatedpnl += pnleth
            log.info(f"[SIMULADO] PnL desta operação: {pnleth:+.6f} ETH | Acumulado: {executor.simulatedpnl:+.6f} ETH")
