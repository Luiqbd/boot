import logging
import asyncio
from decimal import Decimal
from time import time

from web3 import Web3
from telegram import Bot, TelegramAlert

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor

from utils import (
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config,
    to_float,
    get_token_balance
)

from risk_manager import risk_manager

log               = logging.getLogger("sniper")
bot_notify        = Bot(token=config.get("TELEGRAM_TOKEN"))
configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

_recent_pairs      = {}
_PAIR_DUP_INTERVAL = to_float(config.get("PAIR_DUP_INTERVAL"), 5)

def notify(msg: str):
    coro = bot_notify.send_message(
        chat_id=config.get("TELEGRAM_CHAT_ID"),
        text=msg
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)

def safe_notify(
    alert: TelegramAlert | None,
    msg: str,
    loop: asyncio.AbstractEventLoop | None = None
):
    now = time()
    key = hash(msg)
    last = getattr(safe_notify, "_last_msgs", {}).get(key, 0)
    if last + _PAIR_DUP_INTERVAL > now:
        return
    safe_notify._last_msgs = getattr(safe_notify, "_last_msgs", {})
    safe_notify._last_msgs[key] = now

    if alert:
        coro = alert.send_message(
            chat_id=config.get("TELEGRAM_CHAT_ID"),
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
    # 1) Rate limiter
    if rate_limiter.is_paused():
        risk_manager.record_event("pair_skipped", reason="API rate limit pause", dex=dex_info["name"], pair=pair_addr)
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando pares.", loop)
        return

    # 2) Dedupe local
    now = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    if key in _recent_pairs and (now - _recent_pairs[key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Par ignorado localmente: {pair_addr}")
        return
    _recent_pairs[key] = now

    # 3) Novo par detectado
    log.info(f"[Novo par] {dex_info['name']} {pair_addr} {token0}/{token1}")
    risk_manager.record_event("pair_detected", dex=dex_info["name"], pair=pair_addr)

    try:
        # 4) Contexto on‐chain
        web3    = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth    = Web3.to_checksum_address(config["WETH"])
        target  = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth.lower()
            else Web3.to_checksum_address(token0)
        )

        amt_eth = Decimal(str(to_float(config.get("TRADE_SIZE_ETH"), 0.1)))
        if amt_eth <= 0:
            raise ValueError("TRADE_SIZE_ETH inválido")

        dex_client = DexClient(web3, dex_info["router"])
        MIN_LIQ = to_float(config.get("MIN_LIQ_WETH"), 0.5)
        liq_ok  = dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ)
        price   = dex_client.get_token_price(target, weth)
        slip    = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))

        if not liq_ok:
            reason = f"liquidez < {MIN_LIQ} WETH"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Pool ignorada: {reason}", loop)
            return

        MAX_TAX = to_float(config.get("MAX_TAX_PCT"), 10.0)
        if has_high_tax(target, MAX_TAX):
            reason = f"taxa > {MAX_TAX}%"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Token ignorado: {reason}", loop)
            return

        if config.get("BLOCK_UNVERIFIED", False) and not is_contract_verified(target):
            reason = "contrato não verificado"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            return

        TOP = to_float(config.get("TOP_HOLDER_LIMIT"), 30.0)
        if is_token_concentrated(target, top_limit_pct=TOP):
            reason = "alta concentração de supply"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            return

    except Exception as e:
        log.error(f"Erro preparando contexto: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        return

    # 5) Tentativa de compra
    risk_manager.record_event(
        "buy_attempt",
        token=target, amount_eth=float(amt_eth),
        price=float(price), slippage=float(slip)
    )
    exchange   = ExchangeClient(router_address=dex_info["router"])
    trade_exec = TradeExecutor(exchange, dry_run=config.get("DRY_RUN", False))
    safe_exec  = SafeTradeExecutor(executor=trade_exec, risk_manager=risk_manager)

    tx_buy = safe_exec.buy(
        token_in=weth,
        token_out=target,
        amount_eth=float(amt_eth),
        current_price=float(price),
        last_trade_price=None
    )

    if not tx_buy:
        motivo = risk_manager.last_block_reason or "não informado"
        risk_manager.record_event("buy_failed", reason=motivo, token=target)
        risk_manager.register_trade(False, pnl_eth=0.0, direction="buy")
        safe_notify(bot, f"🚫 Compra falhou: {motivo}", loop)
        return

    # 6) Sucesso de compra
    risk_manager.record_event(
        "buy_success",
        token=target, amount_eth=float(amt_eth),
        price=float(price), tx_hash=tx_buy
    )
    risk_manager.register_trade(True, pnl_eth=0.0, direction="buy")
    safe_notify(bot, f"✅ Compra realizada: {target}\nTX: {tx_buy}", loop)

    # 7) Monitoramento para venda
    highest   = price
    entry     = price
    tp_pct    = to_float(config.get("TAKE_PROFIT_PCT"), 0.2)
    sl_pct    = to_float(config.get("STOP_LOSS_PCT"), 0.05)
    trail_pct = to_float(config.get("TRAIL_PCT"), 0.05)

    tp_price  = entry * (1 + tp_pct)
    hard_stop = entry * (1 - sl_pct)
    stop_price = highest * (1 - trail_pct)
    sold      = False

    from discovery import is_discovery_running

    try:
        while is_discovery_running():
            await asyncio.sleep(int(to_float(config.get("INTERVAL"), 3)))
            try:
                price = dex_client.get_token_price(target, weth)
            except Exception:
                continue

            if price > highest:
                highest    = price
                stop_price = highest * (1 - trail_pct)

            if price >= tp_price or price <= stop_price or price <= hard_stop:
                balance = get_token_balance(web3, target, exchange.wallet, exchange.erc20_abi)
                if balance <= 0:
                    break

                tx_sell = safe_exec.sell(
                    token_in=target,
                    token_out=weth,
                    amount_eth=float(balance),
                    current_price=price,
                    last_trade_price=entry
                )
                if tx_sell:
                    risk_manager.record_event(
                        "sell_success",
                        token=target, amount_eth=float(balance),
                        price=price, tx_hash=tx_sell
                    )
                    risk_manager.register_trade(True, pnl_eth=0.0, direction="sell")
                    safe_notify(bot, f"💰 Venda realizada: {target}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record_event("sell_failed", reason=motivo, token=target)
                    risk_manager.register_trade(False, pnl_eth=0.0, direction="sell")
                    safe_notify(bot, f"⚠️ Venda falhou: {motivo}", loop)
                break

    finally:
        if not sold:
            safe_notify(bot, f"⏹ Monitoramento encerrado: {target}", loop)
