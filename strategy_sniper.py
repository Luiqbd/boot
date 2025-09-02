# strategy_sniper.py

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

from utils import (
    is_contract_verified,
    is_token_concentrated,
    has_high_tax,
    get_token_balance,
    rate_limiter,
    configure_rate_limiter_from_config
)
from risk_manager import risk_manager

log = logging.getLogger("sniper")

bot_notify = Bot(token=config["TELEGRAM_TOKEN"])
configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))
risk_manager.record_event = risk_manager._registrar_evento
_PAIR_DUP_INTERVAL = 5
_recent_pairs: dict[tuple[str, str, str], float] = {}


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


def safe_notify(alert: TelegramAlert | Bot | None, msg: str,
                loop: asyncio.AbstractEventLoop | None = None):
    now = time()
    key = hash(msg)
    if getattr(safe_notify, "_last_msgs", {}).get(key, 0) + _PAIR_DUP_INTERVAL > now:
        return
    safe_notify._last_msgs = getattr(safe_notify, "_last_msgs", {})
    safe_notify._last_msgs[key] = now

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


async def on_new_pair(dex_info, pair_addr, token0, token1,
                      bot=None, loop=None):
    # 1) API rate-limit pause?
    if rate_limiter.is_paused():
        risk_manager.record_event(
            "pair_skipped",
            mensagem="API rate limit pause",
            pair=pair_addr,
            origem=getattr(dex_info, "name", "DEX")
        )
        safe_notify(bot, "⏸️ Sniper pausado por limite de API.", loop)
        return

    # 2) dedupe local
    now_ts = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    if key in _recent_pairs and (now_ts - _recent_pairs[key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Ignorando par: {pair_addr}")
        return
    _recent_pairs[key] = now_ts

    # 3) registro de novo par
    dex_name = getattr(dex_info, "name", "DEX")
    log.info(f"[Novo par] {dex_name} {pair_addr} {token0}/{token1}")
    risk_manager.record_event(
        "pair_detected",
        mensagem="Novo par detectado",
        pair=pair_addr,
        origem=dex_name
    )

    # 4) contexto e filtros
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        target = (Web3.to_checksum_address(token1)
                  if token0.lower() == weth.lower()
                  else Web3.to_checksum_address(token0))

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            raise ValueError("TRADE_SIZE_ETH inválido")

        dex_client = DexClient(web3, dex_info["router"])
        MIN_LIQ = float(config.get("MIN_LIQ_WETH", 0.5))
        liq_ok = dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ)

        price = dex_client.get_token_price(target, weth)
        slip = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))

        safe_notify(
            bot,
            f"🔍 Novo par: {pair_addr}\n"
            f"• DEX: {dex_name}\n"
            f"• Alvo: {target}\n"
            f"• Liquidez: {MIN_LIQ} WETH → {'OK' if liq_ok else 'BAIXA'}\n"
            f"• Preço: {price:.10f} WETH\n"
            f"• Slippage sugerida: {slip:.4f}",
            loop
        )

        if not liq_ok:
            risk_manager.record_event(
                "pair_skipped",
                mensagem=f"liquidez < {MIN_LIQ} WETH",
                pair=pair_addr,
                origem="liq_check"
            )
            safe_notify(bot, f"⚠️ Pool ignorada por baixa liquidez.", loop)
            return

        # ... restante dos filtros ...
    except Exception as e:
        log.error(f"Erro nos filtros iniciais: {e}", exc_info=True)
        risk_manager.record_event(
            "error",
            mensagem=str(e),
            pair=pair_addr,
            origem="filter_setup"
        )
        return

# 5) tentativa de compra
    risk_manager.record_event(
        "buy_attempt",
        mensagem="tentativa de compra",
        pair=pair_addr,
        origem="buy_phase"
    )

    # 6) setup e execução da compra
    try:
        exchange = ExchangeClient(
            router_address=getattr(dex_info, "router")
        )
        trade_exec = TradeExecutor(
            exchange_client=exchange,
            dry_run=config["DRY_RUN"]
        )
        safe_exec = SafeTradeExecutor(
            executor=trade_exec,
            risk_manager=risk_manager
        )
    except Exception as e:
        log.error(f"Erro ao criar executor: {e}", exc_info=True)
        risk_manager.record_event(
            "error",
            mensagem=str(e),
            pair=pair_addr,
            origem="exec_setup"
        )
        return

    tx_buy = safe_exec.buy(
        token_in=weth,
        token_out=target,
        amount_eth=amt_eth,
        current_price=price,
        last_trade_price=None,
        amount_out_min=None,
        slippage=slip
    )

    if tx_buy:
        risk_manager.record_event(
            "buy_success",
            mensagem="compra realizada",
            pair=pair_addr,
            origem="buy_phase"
        )
        risk_manager.register_trade(
            success=True,
            pair=pair_addr,
            direction="buy",
            now_ts=int(time()),
        )
        safe_notify(
            bot,
            f"✅ Compra feita: {target}\nTX: {tx_buy}",
            loop
        )
    else:
        motivo = risk_manager.last_block_reason or "não informado"
        risk_manager.record_event(
            "buy_failed",
            mensagem=motivo,
            pair=pair_addr,
            origem="buy_phase"
        )
        risk_manager.register_trade(
            success=False,
            pair=pair_addr,
            direction="buy",
            now_ts=int(time()),
        )
        safe_notify(
            bot,
            f"🚫 Compra falhou: {motivo}",
            loop
        )
        return

    # 7) monitoramento para venda
    highest = price
    entry = price
    tp_pct = float(config.get("TAKE_PROFIT_PCT", 0.2))
    sl_pct = float(config.get("STOP_LOSS_PCT", 0.05))
    trail = float(config.get("TRAIL_PCT", 0.05))

    tp_price = entry * (1 + tp_pct)
    hard_stop = entry * (1 - sl_pct)
    stop_price = highest * (1 - trail)
    sold = False

    from discovery import is_discovery_running
    while is_discovery_running():
        try:
            price = dex_client.get_token_price(target, weth)
        except Exception:
            await asyncio.sleep(1)
            continue

        if price > highest:
            highest = price
            stop_price = highest * (1 - trail)

        if price >= tp_price or price <= stop_price or price <= hard_stop:
            balance = get_token_balance(exchange, target)
            if balance <= 0:
                break

            tx_sell = safe_exec.sell(
                token_in=target,
                token_out=weth,
                amount_eth=Decimal(str(balance)),
                current_price=price,
                last_trade_price=entry
            )
            if tx_sell:
                risk_manager.record_event(
                    "sell_success",
                    mensagem="venda realizada",
                    pair=pair_addr,
                    origem="sell_phase"
                )
                risk_manager.register_trade(
                    success=True,
                    pair=pair_addr,
                    direction="sell",
                    now_ts=int(time()),
                )
                safe_notify(
                    bot,
                    f"💰 Venda feita: {target}\nTX: {tx_sell}",
                    loop
                )
                sold = True
            else:
                motivo = risk_manager.last_block_reason or "não informado"
                risk_manager.record_event(
                    "sell_failed",
                    mensagem=motivo,
                    pair=pair_addr,
                    origem="sell_phase"
                )
                risk_manager.register_trade(
                    success=False,
                    pair=pair_addr,
                    direction="sell",
                    now_ts=int(time()),
                )
                safe_notify(
                    bot,
                    f"⚠️ Venda falhou: {motivo}",
                    loop
                )

            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    if not sold:
        safe_notify(
            bot,
            f"⏹ Monitoramento encerrado: {target}",
            loop
                          )
