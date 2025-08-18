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
    """Envia mensagem ao Telegram de forma compatível com a API assíncrona."""
    try:
        coro = bot_notify.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=msg
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception as e:
        log.error(f"Erro ao enviar notificação: {e}")

def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    """Envia a mensagem via TelegramAlert e também via notify()."""
    if alert:
        try:
            coro = alert._send_async(msg)
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
    amt_in = 10 ** 18
    path = [token, weth]
    try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    from discovery import is_discovery_running
    from telegram_alert import TelegramAlert
    from trade_executor import TradeExecutor
    from safe_trade_executor import SafeTradeExecutor
    from risk_manager import RiskManager

    log.info(f"🎯 Novo par detectado: {pair_addr} ({token0} / {token1}) na DEX {dex_info['name']}")

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    target_token = token1 if token0.lower() == weth.lower() else token0
    amt_eth = Web3.to_wei(float(config["TRADE_SIZE_ETH"]), "ether")
    slippage_bps = int(config["SLIPPAGE_BPS"])
    stop_loss_pct = float(config["STOP_LOSS_PCT"]) / 100
    take_profit_pct = float(config["TAKE_PROFIT_PCT"]) / 100
    trail_pct = stop_loss_pct

    router_contract = web3.eth.contract(address=dex_info["router"], abi=ROUTER_ABI)
    alert = TelegramAlert(bot=bot, chat_id=config["TELEGRAM_CHAT_ID"])

    entry_price = get_token_price_in_weth(router_contract, target_token, weth)
    if not entry_price:
        log.warning(f"❌ Não foi possível obter preço de entrada para {target_token}")
        safe_notify(alert, f"❌ Falha ao obter preço de entrada para {target_token}", loop)
        return

    amount_out_min_val = amount_out_min(router_contract, amt_eth, [weth, target_token], slippage_bps)

    safe_exec = SafeTradeExecutor(
        executor=TradeExecutor(web3=web3, dex_info=dex_info),
        risk_manager=RiskManager()
    )

    buy_tx = safe_exec.buy(
        token_in=weth,
        token_out=target_token,
        amount_eth=amt_eth,
        current_price=entry_price,
        last_trade_price=None,
        amount_out_min=amount_out_min_val
    )

    if buy_tx:
        msg = f"🚀 Compra realizada: {target_token}\nTX: {buy_tx}\n💵 Preço entrada: {entry_price:.6f} WETH"
        log.info(msg)
        safe_notify(alert, msg, loop)
    else:
        warn = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning(warn)
        safe_notify(alert, warn, loop)
        return

    take_profit_price = entry_price * (1 + take_profit_pct)
    highest_price = entry_price
    stop_price = entry_price * (1 - stop_loss_pct)
    sold = False

    log.info(f"📈 Monitorando {target_token} para venda...\n🎯 TP: {take_profit_price:.6f} | 🛑 SL: {stop_price:.6f}")

    while is_discovery_running():
        price = get_token_price_in_weth(router_contract, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(
                token_in=target_token,
                token_out=weth,
                amount_eth=amt_eth,
                current_price=price,
                last_trade_price=entry_price,
                amount_out_min=None
            )
            if sell_tx:
                msg = f"💰 Venda realizada: {target_token}\nTX: {sell_tx}\n📊 Preço saída: {price:.6f} WETH"
                log.info(msg)
                safe_notify(alert, msg, loop)
                sold = True
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                safe_notify(alert, warn, loop)
            break

        await asyncio.sleep(3)

    if not sold and not is_discovery_running():
        msg = f"⏹ Monitoramento encerrado para {target_token} (sniper parado)."
        log.info(msg)
        safe_notify(alert, msg, loop)
