# strategy_sniper.py — Parte 1

import logging
import asyncio
from decimal import Decimal
from time import time
from web3 import Web3

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor

# filtros + rate limiter
from utils import (
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config
)

# singleton do novo risk_manager
from risk_manager import risk_manager

log = logging.getLogger("sniper")
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

_DUPE_INTERVAL = 5
_last_msgs = {}

def notify(msg: str):
    coro = bot_notify.send_message(
        chat_id=config["TELEGRAM_CHAT_ID"],
        text=msg
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)

def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    now = time()
    key = hash(msg)
    if key in _last_msgs and now - _last_msgs[key] < _DUPE_INTERVAL:
        return
    _last_msgs[key] = now

    if alert:
        coro = alert.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=msg
        )
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
        else:
            try:
                asyncio.get_running_loop().create_task(coro)
            except RuntimeError:
                asyncio.run(coro)
    else:
        notify(msg)

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    # pausa por rate limit
    if rate_limiter.is_paused():
        risk_manager.record_event(
            "pair_skipped",
            reason="API rate limit pause",
            dex=dex_info["name"],
            pair=pair_addr
        )
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando pares.", loop)
        return

    now = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    # dup check
    if key in rate_limiter.recent and now - rate_limiter.recent[key] < _DUPE_INTERVAL:
        return
    rate_limiter.recent[key] = now

    log.info(f"Novo par recebido: {dex_info['name']} {pair_addr} {token0}/{token1}")
    risk_manager.record_event(
        "pair_detected",
        dex=dex_info["name"],
        pair=pair_addr,
        token_in=token0,
        token_out=token1
    )

    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        target_token = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth.lower()
            else Web3.to_checksum_address(token0)
        )

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADE_SIZE_ETH inválido; abortando.")
            return

        # checa liquidez
        MIN_LIQ = float(config.get("MIN_LIQ_WETH", 0.5))
        dex_client = DexClient(web3, dex_info["router"])
        if not dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ):
            reason = f"liquidez < {MIN_LIQ} WETH"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Pool ignorada por {reason}", loop)
            return

        # checa taxa
        MAX_TAX = float(config.get("MAX_TAX_PCT", 10.0))
        if has_high_tax(target_token, MAX_TAX):
            reason = f"taxa > {MAX_TAX}%"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Token ignorado por {reason}", loop)
            return

        # contrato verificado
        if not is_contract_verified(target_token, config.get("ETHERSCAN_API_KEY")):
            reason = "contrato não verificado"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            if config.get("BLOCK_UNVERIFIED", False):
                safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
                return

        # concentração de supply
        TOP = float(config.get("TOP_HOLDER_LIMIT", 30.0))
        if is_token_concentrated(target_token, config.get("ETHERSCAN_API_KEY"), TOP):
            reason = "alta concentração de supply"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token com {reason}", loop)
            return

        # preço e slippage
        preco = dex_client.get_token_price(target_token, weth)
        slip = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))

    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        return

    # tentativa de compra
    risk_manager.record_event(
        "buy_attempt",
        token=target_token,
        amount_eth=float(amt_eth),
        price=float(preco),
        slippage=float(slip)
    )

# strategy_sniper.py — Parte 2

    try:
        exchange = ExchangeClient(router_address=dex_info["router"])
        exec = SafeTradeExecutor(
            executor=TradeExecutor(exchange_client=exchange, dry_run=config["DRY_RUN"]),
            risk_manager=risk_manager
        )
    except Exception as e:
        log.error(f"Falha ao criar executor: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        return

    tx_buy = exec.buy(
        token_in=weth,
        token_out=target_token,
        amount_eth=amt_eth,
        current_price=preco,
        last_trade_price=None,
        amount_out_min=None,
        slippage=slip
    )

    if tx_buy:
        risk_manager.record_event(
            "buy_success",
            token=target_token,
            amount_eth=float(amt_eth),
            price=float(preco),
            tx_hash=tx_buy
        )
        # registra PnL e streak
        risk_manager.register_trade(
            success=True,
            token=target_token,
            direction="buy",
            trade_size_eth=float(amt_eth),
            entry_price=float(preco),
            tx_hash=tx_buy
        )
        safe_notify(bot, f"✅ Compra: {target_token}\nTX: {tx_buy}", loop)
    else:
        motivo = risk_manager.last_block_reason or "não informado"
        risk_manager.record_event(
            "buy_failed",
            token=target_token,
            reason=motivo
        )
        risk_manager.register_trade(
            success=False,
            token=target_token,
            direction="buy",
            trade_size_eth=float(amt_eth),
            entry_price=float(preco)
        )
        safe_notify(bot, f"🚫 Compra falhou: {motivo}", loop)
        return

    # monitoramento de preço para vender
    highest = preco
    tp = float(config.get("TAKE_PROFIT_PCT", 0.2))
    sl = float(config.get("STOP_LOSS_PCT", 0.05))
    trail = float(config.get("TRAIL_PCT", 0.05))

    entry = preco
    tp_price = entry * (1 + tp)
    hard_stop = entry * (1 - sl)
    stop_price = highest * (1 - trail)
    sold = False

    from discovery import is_discovery_running
    try:
        while is_discovery_running():
            try:
                price = dex_client.get_token_price(target_token, weth)
            except Exception:
                await asyncio.sleep(1)
                continue

            if price > highest:
                highest = price
                stop_price = highest * (1 - trail)

            if price >= tp_price or price <= stop_price or price <= hard_stop:
                balance = get_token_balance(
                    Web3(Web3.HTTPProvider(config["RPC_URL"])),
                    target_token,
                    exchange.wallet,
                    exchange.erc20_abi
                )
                if balance <= 0:
                    break

                tx_sell = exec.sell(
                    token_in=target_token,
                    token_out=weth,
                    amount_eth=Decimal(str(balance)),
                    current_price=price,
                    last_trade_price=entry
                )
                if tx_sell:
                    risk_manager.record_event(
                        "sell_success",
                        token=target_token,
                        amount_eth=float(balance),
                        price=float(price),
                        tx_hash=tx_sell
                    )
                    risk_manager.register_trade(
                        success=True,
                        token=target_token,
                        direction="sell",
                        trade_size_eth=float(balance),
                        entry_price=float(entry),
                        exit_price=float(price),
                        tx_hash=tx_sell
                    )
                    safe_notify(bot, f"💰 Venda: {target_token}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record_event(
                        "sell_failed",
                        token=target_token,
                        reason=motivo
                    )
                    risk_manager.register_trade(
                        success=False,
                        token=target_token,
                        direction="sell",
                        trade_size_eth=float(balance),
                        entry_price=float(entry)
                    )
                    safe_notify(bot, f"⚠️ Venda falhou: {motivo}", loop)
                break

            await asyncio.sleep(int(config.get("INTERVAL", 3)))
    finally:
        if not sold:
            safe_notify(bot, f"⏹ Monitoramento encerrado para {target_token}.", loop)
