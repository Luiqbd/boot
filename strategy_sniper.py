import logging
import asyncio
from decimal import Decimal
from time import time
from web3 import Web3

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert, send_report
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor

from utils import (
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config,
    get_token_balance
)
from risk_manager import risk_manager

log = logging.getLogger("sniper")
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

# cache local para evitar duplicatas rápidas (agressivo → 3s em vez de 5s)
_PAIR_DUP_INTERVAL = 3


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


def safe_notify(alert: TelegramAlert | None, msg: str,
                loop: asyncio.AbstractEventLoop | None = None):
    """Evita spam e envia notificação via TelegramAlert ou Bot."""
    now = time()
    key = hash(msg)
    last = getattr(safe_notify, "_last_msgs", {}).get(key, 0)
    if now - last < _PAIR_DUP_INTERVAL:
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


async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    # 1) pausa por rate limiter
    if rate_limiter.is_paused():
        reason = "API rate limit pause"
        risk_manager.record_event("pair_skipped",
                                  reason=reason,
                                  dex=dex_info["name"],
                                  pair=pair_addr)
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando pares.", loop)
        send_report(bot_notify, "⏸️ Sniper pausado: limite de API. Ignorando pares.")
        return

    # 2) filtro de duplicata local
    now = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    if key in _recent_pairs and (now - _recent_pairs[key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Par ignorado localmente: {pair_addr} {token0}/{token1}")
        return
    _recent_pairs[key] = now

    # registra o par
    log.info(f"[Novo par] {dex_info['name']} {pair_addr} {token0}/{token1}")
    risk_manager.record_event(
        "pair_detected",
        dex=dex_info["name"],
        pair=pair_addr,
        token_in=token0,
        token_out=token1
    )
    send_report(
        bot_notify,
        f"🆕 Novo par detectado em {dex_info['name']}\n"
        f"Par: {pair_addr}\n"
        f"Tokens: {token0}/{token1}"
    )

    # prepara contexto
    try:
        web3      = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth_addr = Web3.to_checksum_address(config["WETH"])
        wallet    = Web3.to_checksum_address(config["WALLET_ADDRESS"])
        target    = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth_addr.lower()
            else Web3.to_checksum_address(token0)
        )

        # 3) ajuste dinâmico de tamanho de trade baseado no saldo de WETH
        balance_wei = get_token_balance(web3, weth_addr, wallet)
        balance_eth = Decimal(balance_wei) / Decimal(10 ** 18)

        conf_size = Decimal(str(config.get("TRADE_SIZE_ETH", 0.2)))
        # usa até 90% do saldo disponível
        amt_eth = min(conf_size, (balance_eth * Decimal("0.9")).quantize(conf_size))

        if amt_eth <= Decimal("0"):
            reason = f"Saldo insuficiente ({balance_eth:.6f} ETH)"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ {reason}", loop)
            send_report(bot_notify, f"⚠️ {reason} — {pair_addr}")
            return

        log.info(f"Usando {amt_eth} ETH para comprar em {pair_addr} (saldo = {balance_eth:.6f} ETH)")

        dex_client = DexClient(web3, dex_info["router"])

        # 4) limiar de liquidez abaixado (agressivo → aceita pools ≥ 0.25 WETH)
        MIN_LIQ = float(config.get("MIN_LIQ_WETH", 0.25))
        liq_ok = dex_client.has_min_liquidity(pair_addr, weth_addr, MIN_LIQ)

        price = dex_client.get_token_price(target, weth_addr)
        slip  = dex_client.calc_dynamic_slippage(pair_addr, weth_addr, float(amt_eth))

        if not liq_ok:
            reason = f"liquidez < {MIN_LIQ} WETH"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Pool ignorada: {reason}", loop)
            send_report(bot_notify, f"⚠️ Pool ignorada: {reason} — {pair_addr}")
            return

        # 5) permite taxa maior (agressivo → até 15%)
        MAX_TAX = float(config.get("MAX_TAX_PCT", 15.0))
        if is_token_concentrated(target, MAX_TAX):
            reason = f"taxa > {MAX_TAX}%"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Token ignorado: {reason}", loop)
            send_report(bot_notify, f"⚠️ Token ignorado por tax > {MAX_TAX}% — {target}")
            return

        # 6) validação de contrato segue igual (bloqueia unverified se configurado)
        if not is_contract_verified(target) \
           and config.get("BLOCK_UNVERIFIED", False):
            reason = "contrato não verificado"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            send_report(bot_notify, f"🚫 Token bloqueado: {reason} — {target}")
            return

        # 7) aceita concentração de holder mais alta (agressivo → até 50%)
        TOP = float(config.get("TOP_HOLDER_LIMIT", 50.0))
        if is_token_concentrated(target, TOP):
            reason = "alta concentração de supply"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            send_report(bot_notify, f"🚫 Token bloqueado: {reason} — {target}")
            return

    except Exception as e:
        log.error(f"Erro preparando contexto: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        send_report(bot_notify, f"❌ Erro preparando contexto: {e}")
        return

    # tentativa de compra
    risk_manager.record_event(
        "buy_attempt",
        token=target,
        amount_eth=float(amt_eth),
        price=float(price),
        slippage=float(slip)
    )
    send_report(
        bot_notify,
        f"💰 Tentativa de compra iniciada:\n"
        f"Token: {target}\n"
        f"Valor em ETH: {amt_eth}\n"
        f"Preço: {price}\n"
        f"Slippage: {slip}"
    )

# instancia executores
    try:
        exchange = ExchangeClient(
            web3=web3,
            private_key=config["PRIVATE_KEY"],
            router_address=dex_info["router"],
            chain_id=int(config.get("CHAIN_ID", 8453))
        )
        trade_exec = TradeExecutor(
            exchange_client=exchange,
            dry_run=config["DRY_RUN"]
        )
        safe_exec = SafeTradeExecutor(executor=trade_exec, risk_manager=risk_manager)
    except Exception as e:
        log.error(f"Erro ao criar executor: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        send_report(bot_notify, f"❌ Erro ao criar executor: {e}")
        return

    # executa compra com valor em ETH já ajustado
    tx_buy = safe_exec.buy(
        token_in=weth_addr,
        token_out=target,
        amount_eth=amt_eth,
        current_price=price,
        last_trade_price=None
    )

    if tx_buy:
        risk_manager.record_event(
            "buy_success",
            token=target,
            amount_eth=float(amt_eth),
            price=float(price),
            tx_hash=tx_buy
        )
        risk_manager.register_trade(
            success=True,
            token=target,
            direction="buy",
            trade_size_eth=float(amt_eth),
            entry_price=float(price),
            tx_hash=tx_buy
        )
        safe_notify(bot, f"✅ Compra realizada: {target}\nTX: {tx_buy}", loop)
        send_report(bot_notify, f"✅ Compra realizada:\nToken: {target}\nTX: {tx_buy}")
    else:
        motivo = risk_manager.last_block_reason or "não informado"
        risk_manager.record_event("buy_failed", token=target, reason=motivo)
        risk_manager.register_trade(
            success=False,
            token=target,
            direction="buy",
            trade_size_eth=float(amt_eth),
            entry_price=float(price)
        )
        safe_notify(bot, f"🚫 Compra falhou: {motivo}", loop)
        send_report(bot_notify, f"🚫 Compra falhou:\nToken: {target}\nMotivo: {motivo}")
        return

    # inicia monitoramento para venda
    highest = price

    # 8) TAKE_PROFIT_PCT mais agressivo: 10%
    tp_pct = float(config.get("TAKE_PROFIT_PCT", 0.1))
    # 9) STOP_LOSS_PCT mais agressivo: 2%
    sl_pct = float(config.get("STOP_LOSS_PCT", 0.02))
    # 10) TRAIL_PCT mais agressivo: 2%
    trail = float(config.get("TRAIL_PCT", 0.02))

    entry = price
    tp_price = entry * (1 + tp_pct)
    hard_stop = entry * (1 - sl_pct)
    stop_price = highest * (1 - trail)
    sold = False

    send_report(
        bot_notify,
        "🚀 Iniciando monitoramento de venda:\n"
        f"Token: {target}\n"
        f"Entry: {entry:.6f}\n"
        f"TP: {tp_price:.6f} ({tp_pct*100:.1f}%)\n"
        f"SL: {hard_stop:.6f} ({sl_pct*100:.1f}%)\n"
        f"Trail: {trail*100:.1f}%"
    )

    from discovery import is_discovery_running
    try:
        while is_discovery_running():
            try:
                price = dex_client.get_token_price(target, weth_addr)
            except Exception:
                await asyncio.sleep(1)
                continue

            if price > highest:
                highest = price
                stop_price = highest * (1 - trail)

            if price >= tp_price or price <= stop_price or price <= hard_stop:
                # consulta saldo do token em wei
                try:
                    balance_wei = get_token_balance(web3, target, exchange.wallet)
                except Exception as e:
                    log.error(f"Erro ao obter saldo para {target}: {e}", exc_info=True)
                    break

                if balance_wei <= 0:
                    break

                # converte wei para unidades humanas de token
                decimals = exchange.get_token_decimals(target)
                balance_tokens = Decimal(balance_wei) / (Decimal(10) ** decimals)

                tx_sell = safe_exec.sell(
                    token_in=target,
                    token_out=weth_addr,
                    amount_tokens=balance_tokens,
                    current_price=price,
                    last_trade_price=entry
                )

                if tx_sell:
                    risk_manager.record_event(
                        "sell_success",
                        token=target,
                        amount_eth=float(balance_tokens),
                        price=float(price),
                        tx_hash=tx_sell
                    )
                    risk_manager.register_trade(
                        success=True,
                        token=target,
                        direction="sell",
                        trade_size_eth=float(balance_tokens),
                        entry_price=float(entry),
                        exit_price=float(price),
                        tx_hash=tx_sell
                    )
                    safe_notify(bot, f"💰 Venda realizada: {target}\nTX: {tx_sell}", loop)
                    send_report(bot_notify, f"💰 Venda realizada:\nToken: {target}\nTX: {tx_sell}")
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record_event(
                        "sell_failed",
                        token=target,
                        reason=motivo
                    )
                    risk_manager.register_trade(
                        success=False,
                        token=target,
                        direction="sell",
                        trade_size_eth=float(balance_tokens),
                        entry_price=float(entry)
                    )
                    safe_notify(bot, f"⚠️ Venda falhou: {motivo}", loop)
                    send_report(bot_notify, f"⚠️ Venda falhou:\nToken: {target}\nMotivo: {motivo}")
                break

            # 11) INTERVAL mais agressivo: 1 segundo
            await asyncio.sleep(int(config.get("INTERVAL", 1)))
    finally:
        if not sold:
            safe_notify(bot, f"⏹ Monitoramento encerrado: {target}", loop)
            send_report(bot_notify, f"⏹ Monitoramento encerrado:\nToken: {target}")
