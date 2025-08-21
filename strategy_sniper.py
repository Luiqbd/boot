import logging
import math
import datetime
import asyncio
from web3 import Web3
from eth_account import Account

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager

log = logging.getLogger("sniper")

# === Notificador direto pelo token/chat_id ===
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

def notify(msg: str):
    """
    Envia mensagem ao Telegram de forma compatível com a API assíncrona,
    evitando warnings de 'never awaited'.
    """
    try:
        coro = bot_notify.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=msg
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)  # agenda no loop existente
        except RuntimeError:
            asyncio.run(coro)       # executa se não houver loop
    except Exception as e:
        log.error(f"Erro ao enviar notificação: {e}")

# === Envio seguro de mensagens (funciona com ou sem loop ativo) ===
def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    """
    Envia a mensagem via TelegramAlert (assíncrono) e também via notify().
    """
    if alert:
        try:
            coro = alert._send_async(msg)  # usa o pipeline de chunk + retries do TelegramAlert
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                try:
                    running_loop = asyncio.get_running_loop()
                    running_loop.create_task(coro)
                except RuntimeError:
                    asyncio.run(coro)
        except Exception as e:
            log.error(f"Falha ao agendar envio para alerta Telegram: {e}", exc_info=True)
    try:
        notify(msg)
    except Exception as e:
        log.error(f"Falha no notify(): {e}", exc_info=True)

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
    amt_in = 10 ** 18  # 1 token (em 18 decimais) para cotação inversa token->WETH
    path = [token, weth]
    try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    # ... [trecho inicial igual à Parte 1, já enviado] ...

    from discovery import is_discovery_running
    sold = False
    while is_discovery_running():
        price = get_token_price_in_weth(router_contract, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(target_token, weth, amt_eth, price, entry_price)
            if sell_tx:
                msg = f"💰 Venda realizada: {target_token}\nTX: {sell_tx}"
                log.info(f"💰 Venda executada — TX: {sell_tx}")
                safe_notify(alert, msg, loop)
                sold = True
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                safe_notify(alert, warn, loop)
            break

        await asyncio.sleep(3)

    # Caso o sniper seja parado antes da venda
    if not sold and not is_discovery_running():
        msg = f"⏹ Monitoramento encerrado para {target_token} (sniper parado)."
        log.info(msg)
        safe_notify(alert, msg, loop)
