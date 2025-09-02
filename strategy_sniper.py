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
    has_high_tax,             # assegura filtro de taxa
    get_token_balance,        # usado no monitoramento de venda
    rate_limiter,
    configure_rate_limiter_from_config
)

from risk_manager import risk_manager

log = logging.getLogger("sniper")

# Bot para notificações diretas (assíncrono, pgram v20)
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

# Configura rate limiter e a função de notificação
configure_rate_limiter_from_config(config)

# Cache local para evitar mensagens duplicadas em janelas curtas
_PAIR_DUP_INTERVAL = 5
_recent_pairs: dict[tuple[str, str, str], float] = {}


def notify(msg: str):
    """
    Notifica usando o bot padrão em contexto assíncrono ou síncrono.
    """
    coro = bot_notify.send_message(
        chat_id=config["TELEGRAM_CHAT_ID"],
        text=msg
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def safe_notify(alert: TelegramAlert | Bot | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    """
    Notifica com deduplicação rápida para evitar spam de mensagens iguais.
    Aceita tanto TelegramAlert quanto Bot do python-telegram-bot.
    """
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


# Conecta o rate limiter ao mecanismo de notificação
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))


async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    """
    Handler principal para novos pares detectados.
    Aplica filtros: pausa (rate limit), duplicata, liquidez, taxa, verificação de contrato e concentração.
    Em caso de aprovação, tenta comprar e inicia monitoramento para venda.
    """
    # 1) pausa por rate limiter
    if rate_limiter.is_paused():
        risk_manager.record_event("pair_skipped", reason="API rate limit pause", dex=dex_info.get("name"), pair=pair_addr)
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando pares.", loop)
        return

    # 2) filtro de duplicata local (mesmo par/token em curto intervalo)
    now = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    if key in _recent_pairs and (now - _recent_pairs[key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Par ignorado localmente: {pair_addr} {token0}/{token1}")
        return
    _recent_pairs[key] = now

    # 3) registro do par detectado
    dex_name = dex_info.get("name", "DEX")
    log.info(f"[Novo par] {dex_name} {pair_addr} {token0}/{token1}")
    risk_manager.record_event(
        "pair_detected",
        dex=dex_name,
        pair=pair_addr,
        token_in=token0,
        token_out=token1
    )

    # 4) prepara contexto e aplica filtros
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])

        # Define o token alvo (compra com WETH → token alvo)
        target = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth.lower()
            else Web3.to_checksum_address(token0)
        )

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            raise ValueError("TRADE_SIZE_ETH inválido")

        dex_client = DexClient(web3, dex_info["router"])

        # Liquidez mínima em WETH
        MIN_LIQ = float(config.get("MIN_LIQ_WETH", 0.5))
        liq_ok = dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ)

        # Preço atual e slippage dinâmica
        price = dex_client.get_token_price(target, weth)
        slip = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))

        safe_notify(
            bot,
            f"🔍 Novo par: {pair_addr}\n"
            f"• DEX: {dex_name}\n"
            f"• Alvo: {target}\n"
            f"• Liquidez min req: {MIN_LIQ} WETH | status: {'ok' if liq_ok else 'baixa'}\n"
            f"• Preço: {price:.10f} WETH\n"
            f"• Slippage sugerida: {slip:.4f}",
            loop
        )

        if not liq_ok:
            reason = f"liquidez < {MIN_LIQ} WETH"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Pool ignorada: {reason}", loop)
            return

        # Taxa máxima aplicada
        MAX_TAX = float(config.get("MAX_TAX_PCT", 10.0))
        if has_high_tax(target, MAX_TAX):
            reason = f"taxa > {MAX_TAX}%"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"⚠️ Token ignorado: {reason}", loop)
            return

        # Contrato verificado (bloqueia não verificados se configurado)
        if config.get("BLOCK_UNVERIFIED", False) and not is_contract_verified(
            target, config.get("ETHERSCAN_API_KEY")
        ):
            reason = "contrato não verificado"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            return

        # Concentração dos top holders (ordem corrigida)
        TOP_LIMIT = float(config.get("TOP_HOLDER_LIMIT", 30.0))
        # Correção: (token, limit_pct, api_key) — e não (token, api_key, limit_pct)
        if is_token_concentrated(target, TOP_LIMIT, config.get("ETHERSCAN_API_KEY")):
            reason = f"alta concentração de supply (>{TOP_LIMIT}%)"
            risk_manager.record_event("pair_skipped", reason=reason, pair=pair_addr)
            safe_notify(bot, f"🚫 Token bloqueado: {reason}", loop)
            return

    except Exception as e:
        log.error(f"Erro preparando contexto: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
        return

    # 5) tentativa de compra: registra intenção
    risk_manager.record_event(
        "buy_attempt",
        token=target,
        amount_eth=float(amt_eth),
        price=float(price),
        slippage=float(slip)
    )

    # 6) setup do executor e execução da compra
    try:
        exchange = ExchangeClient(router_address=dex_info["router"])
        trade_exec = TradeExecutor(exchange_client=exchange, dry_run=config["DRY_RUN"])
        safe_exec = SafeTradeExecutor(executor=trade_exec, risk_manager=risk_manager)
    except Exception as e:
        log.error(f"Erro ao criar executor: {e}", exc_info=True)
        risk_manager.record_event("error", reason=str(e), pair=pair_addr)
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
        return

    # 7) monitoramento para venda (TP, SL e trailing)
    highest = price
    tp_pct = float(config.get("TAKE_PROFIT_PCT", 0.2))   # 20% por padrão (0.2 = 20%)
    sl_pct = float(config.get("STOP_LOSS_PCT", 0.05))    # 5% stop loss
    trail = float(config.get("TRAIL_PCT", 0.05))         # 5% trailing

    entry = price
    tp_price = entry * (1 + tp_pct)
    hard_stop = entry * (1 - sl_pct)
    stop_price = highest * (1 - trail)
    sold = False

    from discovery import is_discovery_running
    try:
        while is_discovery_running():
            try:
                price = dex_client.get_token_price(target, weth)
            except Exception:
                await asyncio.sleep(1)
                continue

            if price > highest:
                highest = price
                stop_price = highest * (1 - trail)

            # Condições de venda: take profit, trailing stop ou hard stop
            if price >= tp_price or price <= stop_price or price <= hard_stop:
                balance = get_token_balance(
                    web3, target,
                    wallet_address=exchange.wallet,
                    erc20_abi=exchange.erc20_abi
                )
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
                        token=target,
                        amount_eth=float(balance),
                        price=float(price),
                        tx_hash=tx_sell
                    )
                    risk_manager.register_trade(
                        success=True,
                        token=target,
                        direction="sell",
                        trade_size_eth=float(balance),
                        entry_price=float(entry),
                        exit_price=float(price),
                        tx_hash=tx_sell
                    )
                    safe_notify(bot, f"💰 Venda realizada: {target}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record_event("sell_failed", token=target, reason=motivo)
                    risk_manager.register_trade(
                        success=False,
                        token=target,
                        direction="sell",
                        trade_size_eth=float(balance),
                        entry_price=float(entry)
                    )
                    safe_notify(bot, f"⚠️ Venda falhou: {motivo}", loop)
                break

            await asyncio.sleep(int(config.get("INTERVAL", 3)))
    finally:
        if not sold:
            safe_notify(bot, f"⏹ Monitoramento encerrado: {target}", loop)
