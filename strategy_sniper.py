import logging
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

def get_token_balance(web3: Web3, token_address: str, owner_address: str, erc20_abi: list) -> Decimal:
    """Consulta saldo de um token ERC20 em unidades humanas."""
    try:
        token = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)
        raw_balance = token.functions.balanceOf(Web3.to_checksum_address(owner_address)).call()
        decimals = token.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10 ** decimals)
    except Exception as e:
        log.error(f"Erro ao obter saldo do token {token_address}: {e}")
        return Decimal(0)

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    log.info(f"Novo par recebido: {dex_info['name']} {pair_addr} {token0}/{token1}")

    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        if token0.lower() == weth.lower():
            target_token = Web3.to_checksum_address(token1)
        else:
            target_token = Web3.to_checksum_address(token0)

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADE_SIZE_ETH inválido; abortando.")
            return

        dex_client = DexClient(web3, dex_info["router"])
    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    min_liq_ok = dex_client.has_min_liquidity(target_token, weth, config.get("MIN_LIQ_WETH", 0.5))
    preco_atual = dex_client.get_token_price(target_token, weth)

    log.info(f"[Pré-Risk] {token0}/{token1} preço={preco_atual} ETH | size={amt_eth}ETH | liq_ok={min_liq_ok}")

    try:
        exchange_client = ExchangeClient(router_address=dex_info["router"])
        trade_exec = TradeExecutor(exchange_client=exchange_client, dry_run=config["DRY_RUN"])
        safe_exec = SafeTradeExecutor(executor=trade_exec, risk_manager=risk_manager)
    except Exception as e:
        log.error(f"Falha ao criar ExchangeClient/Executor: {e}", exc_info=True)
        return

    tx_buy = safe_exec.buy(
        token_in=weth,
        token_out=target_token,
        amount_eth=amt_eth,
        current_price=preco_atual,
        last_trade_price=None,
        amount_out_min=None
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
        price = dex_client.get_token_price(target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            # Obtém saldo atual de tokens antes de vender
            token_balance = get_token_balance(
                web3, target_token, exchange_client.wallet, exchange_client.erc20_abi
            )
            if token_balance <= 0:
                log.warning("Saldo do token é zero — nada para vender.")
                break

            tx_sell = safe_exec.sell(
                token_in=target_token,
                token_out=weth,
                amount_eth=token_balance,
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
