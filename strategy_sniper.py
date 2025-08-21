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

# ======== NOVO BLOCO DE LOG CONSOLIDADO ========

trade_log = []

def log_event(msg):
    ts_msg = f"[{_now_iso()}] {msg}"
    log.info(ts_msg)
    trade_log.append(ts_msg)

def flush_report(alert):
    """Envia relatório consolidado pro Telegram e limpa o log"""
    if trade_log:
        alert.send("📊 RELATÓRIO DA OPERAÇÃO:\n" + "\n".join(trade_log))
        trade_log.clear()

# ===============================================

def amount_out_min(router, amt_in_wei, path, slippage_bps):
    out = router.functions.getAmountsOut(amt_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_price_weth(router, token, weth):
    """Retorna o preço em WETH por 1 unidade do token (ETH-per-token)."""
    try:
        out = router.functions.getAmountsOut(10**18, [token, weth]).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log_event(f"⚠️ Falha ao obter preço: {e}")
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
    if executor is None:
        raise RuntimeError("Executor não passado em on_new_pair()")

    alert = TelegramAlert(bot, chat_id=int(config["TELEGRAM_CHAT_ID"]))
    dex = DexClient(dex_info)
    w3 = executor.w3
    router = w3.eth.contract(address=dex.router, abi=ROUTER_ABI)
    exchange = ExchangeClient(w3, router)
    executor.set_exchange_client(exchange)

    # Define token alvo e WETH
    if dex.is_weth(token1):
        target, weth = token0, token1
    else:
        target, weth = token1, token0

    log_event(f"Novo par detectado: {pair_addr} | Target: {target}")

    amt_eth = executor.trade_size
    entry_price = get_price_weth(router, target, weth)
    log_event(f"Preço de entrada obtido: {entry_price}")

    if entry_price is None or entry_price <= 0:
        log_event(f"⚠️ Preço de entrada inválido para {target}. Abortando.")
        flush_report(alert)
        return

    buy_amount_in_wei = int(amt_eth * 1e18)
    min_out_buy = amount_out_min(router, buy_amount_in_wei, [weth, target], executor.slippage_bps)
    log_event(f"Calculado min_out_buy: {min_out_buy}")

    # Tentativa de compra
    buy_tx = await executor.buy(path=[weth, target], amount_in_wei=buy_amount_in_wei, amount_out_min=min_out_buy)
    if not buy_tx:
        log_event(f"⚠️ Compra não realizada: {target}")
        flush_report(alert)
        return

    log_event(f"🛒 Compra confirmada: {target} | TX: {buy_tx}")

    # Monitoramento de venda
    highest = entry_price
    trail_pct = executor.trail_pct / 100
    tp_price  = entry_price * (1 + executor.take_profit_pct / 100)
    stop_px   = highest * (1 - trail_pct)

    from discovery import is_discovery_running
    sold = False
    final_price = entry_price

    while is_discovery_running():
        price = get_price_weth(router, target, weth)
        if price is None:
            await asyncio.sleep(1)
            continue

        final_price = price

        if price > highest:
            highest = price
            stop_px = highest * (1 - trail_pct)
            log_event(f"📈 Novo topo: {highest} | Stop ajustado: {stop_px}")

        if price >= tp_price or price <= stop_px:
            sell_amount_in_wei = int(amt_eth * 1e18)
            min_out_sell_weth = math.floor(price * 1e18 * (1 - executor.slippage_bps / 10_000))

            sell_tx = await executor.sell(path=[target, weth], amount_in_wei=sell_amount_in_wei, min_out=min_out_sell_weth)
            if sell_tx:
                log_event(f"💰 Venda realizada: {target} | TX: {sell_tx}")
                sold = True
            else:
                log_event(f"⚠️ Venda bloqueada: {target}")
            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    if not sold:
        log_event(f"⏹ Monitoramento finalizado para {target} (sniper parado).")

    # Atualiza PnL simulado
    if getattr(executor, "dry_run", False):
        if not hasattr(executor, "simulated_pnl"):
            executor.simulated_pnl = 0.0
        if entry_price and final_price:
            pnl_eth = amt_eth * (final_price / entry_price - 1.0)
            executor.simulated_pnl += pnl_eth
            log_event(f"[SIMULADO] PnL: {pnl_eth:+.6f} ETH | Acumulado: {executor.simulated_pnl:+.6f} ETH")

    # Envia relatório consolidado
    flush_report(alert)
