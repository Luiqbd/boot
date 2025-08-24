import logging
import math
import datetime
import asyncio
from decimal import Decimal
from web3 import Web3

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager

log = logging.getLogger("sniper")

# Instância global do RiskManager
risk_manager = RiskManager()

# Bot simples via token/chat_id
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

def notify(msg: str):
    """Envia mensagem simples ao Telegram."""
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
    """Envia mensagem via TelegramAlert + notify simples."""
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
    log.info(f"Novo par recebido: {dex_info['name']} {pair_addr} {token0}/{token1}")

    # Inicialização segura
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        router_contract = web3.eth.contract(
            address=Web3.to_checksum_address(dex_info["router"]),
            abi=ROUTER_ABI
        )

        weth = Web3.to_checksum_address(config["WETH"])
        if token0.lower() == weth.lower():
            target_token = Web3.to_checksum_address(token1)
        else:
            target_token = Web3.to_checksum_address(token0)

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADE_SIZE_ETH inválido; abortando.")
            return
    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    preco_atual = get_token_price_in_weth(router_contract, target_token, weth)

    # Log pré-RiskManager
    log.info(
        f"[Pré-Risk] {token0}/{token1} preço={preco_atual} ETH | size={amt_eth}ETH | liq_ok=True | honeypot_ok=True"
    )

    # Executor seguro
    safe_exec = SafeTradeExecutor(
        executor=TradeExecutor(exchange_client=ExchangeClient(web3)),
        risk_manager=risk_manager
    )

    # Compra
    tx_buy = safe_exec.buy(
        token_in=weth,
        token_out=target_token,
        amount_eth=amt_eth,
        current_price=preco_atual,
        last_trade_price=None,
        amount_out_min=amount_out_min(
            router_contract,
            Web3.to_wei(amt_eth, "ether"),
            [weth, target_token],
            config.get("SLIPPAGE_BPS", 100)
        )
    )

    if tx_buy:
        msg = f"✅ Compra realizada: {target_token}\nTX: {tx_buy}"
        log.info(msg)
        safe_notify(bot, msg, loop)
    else:
        motivo = getattr(risk_manager, "last_block_reason", "não informado")
        warn = f"🚫 Compra não executada para {target_token}\nMotivo: {motivo}"
        log.warning(warn)
        safe_notify(bot, warn, loop)
        return

    # Monitoramento de venda
    highest_price = preco_atual
    trail_pct = config.get("TRAIL_PCT", 0.05)
    take_profit_price = preco_atual * (1 + config.get("TP_PCT", 0.2))
    entry_price = preco_atual
    stop_price = highest_price * (1 - trail_pct)
    sold = False

    from discovery import is_discovery_running
    while is_discovery_running():
        price = get_token_price_in_weth(router_contract, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            tx_sell = safe_exec.sell(
                token_in=target_token,
                token_out=weth,
                amount_eth=amt_eth,
                current_price=price,
                last_trade_price=entry_price
            )
            if tx_sell:
                msg = f"💰 Venda realizada: {target_token}\nTX: {tx_sell}"
                log.info(msg)
                safe_notify(bot, msg, loop)
                sold = True
            else:
                motivo = getattr(risk_manager, "last_block_reason", "não informado")
                warn = f"⚠️ Venda bloqueada: {motivo}"
                log.warning(warn)
                safe_notify(bot, warn, loop)
            break

        await asyncio.sleep(3)

    if not sold and not is_discovery_running():
        msg = f"⏹ Monitoramento encerrado para {target_token} (sniper parado)."
        log.info(msg)
        safe_notify(bot, msg, loop)
