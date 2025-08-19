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

def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(router, amt_in_wei, path, slippage_bps):
    out = router.functions.getAmountsOut(amt_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_price_weth(router, token, weth):
    try:
        out = router.functions.getAmountsOut(10**18, [token, weth]).call()[-1]
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
    executor=None
):
    """
    Callback do discovery.py quando um novo par surge.
    executor: instância de RealTradeExecutor ou SafeTradeExecutor.
    """
    if executor is None:
        raise RuntimeError("Executor não passado em on_new_pair()")

    # prepara notificação
    alert = TelegramAlert(bot, chat_id=int(config["TELEGRAM_CHAT_ID"]))

    # monta cliente DEX e injeta no executor
    dex = DexClient(dex_info)
    w3 = executor.w3
    router = w3.eth.contract(address=dex.router, abi=ROUTER_ABI)
    exchange = ExchangeClient(w3, router)
    executor.set_exchange_client(exchange)

    # define token alvo e WETH
    if dex.is_weth(token1):
        target, weth = token0, token1
    else:
        target, weth = token1, token0

    # valores de trade
    amt_eth     = executor.trade_size
    entry_price = get_price_weth(router, target, weth)
    min_out     = amount_out_min(
        router,
        int(amt_eth * 1e18),
        [weth, target],
        executor.slippage_bps
    )

    # 1) executa compra
    buy_tx = await executor.buy(
        path=[weth, target],
        amount_in_wei=int(amt_eth * 1e18),
        amount_out_min=min_out,
        current_price=entry_price,
        last_trade_price=entry_price
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
    trail_pct = executor.trail_pct / 100
    tp_price  = entry_price * (1 + executor.take_profit_pct / 100)
    stop_px   = highest * (1 - trail_pct)

    from discovery import is_discovery_running
    sold = False
    final_price = entry_price  # valor final padrão caso não haja venda

    while is_discovery_running():
        price = get_price_weth(router, target, weth)
        if price is None:
            await asyncio.sleep(1)
            continue

        if price > highest:
            highest = price
            stop_px = highest * (1 - trail_pct)

        if price >= tp_price or price <= stop_px:
            sell_tx = await executor.sell(
                path=[target, weth],
                amount_in_wei=int(amt_eth * 1e18),
                min_out=math.floor(price * 1e18 * (1 - executor.slippage_bps/10_000)),
                current_price=price,
                last_trade_price=entry_price
            )
            final_price = price
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
        final_price = price  # mantém o último preço visto

    # se parou sem vender
    if not sold:
        msg = f"⏹ Monitoramento finalizado para {target} (sniper parado)."
        log.info(msg)
        alert.send(msg)

    # 3) atualiza PnL simulado se estiver em dry_run
    if getattr(executor, "dry_run", False):
        if not hasattr(executor, "simulated_pnl"):
            executor.simulated_pnl = 0.0
        pnl = (final_price - entry_price) * amt_eth
        executor.simulated_pnl += pnl
        log.info(f"[SIMULADO] PnL acumulado: {executor.simulated_pnl:.6f} ETH")
