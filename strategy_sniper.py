import logging
import asyncio
import traceback
from decimal import Decimal
from time import time
from web3 import Web3

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from dex import DexClient, DexVersion
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor

from utils import (
    is_contract_verified,
    is_token_concentrated,
    has_high_tax,
    get_token_balance,
    rate_limiter,
    configure_rate_limiter_from_config,
    escape_md_v2
)

log = logging.getLogger("sniper")

bot_notify = Bot(token=config["TELEGRAM_TOKEN"])
configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

_PAIR_DUP_INTERVAL = 5
_recent_pairs: dict[tuple[str, str, str], float] = {}


def notify(msg: str):
    """
    Envia mensagem direta ao Telegram escapando MarkdownV2.
    """
    escaped = escape_md_v2(msg)
    coro = bot_notify.send_message(
        chat_id=config["TELEGRAM_CHAT_ID"],
        text=escaped,
        parse_mode="MarkdownV2"
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


def safe_notify(
    alert: TelegramAlert | Bot | None,
    msg: str,
    loop: asyncio.AbstractEventLoop | None = None
):
    """
    Envia mensagem com dedupe e escapando MarkdownV2.
    """
    now = time()
    key = hash(msg)
    last = getattr(safe_notify, "_last_msgs", {})
    if last.get(key, 0) + _PAIR_DUP_INTERVAL > now:
        return
    last[key] = now
    safe_notify._last_msgs = last

    escaped = escape_md_v2(msg)

    if alert:
        coro = alert.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=escaped,
            parse_mode="MarkdownV2"
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


async def on_new_pair(
    dex_info,
    pair_addr,
    token0,
    token1,
    bot=None,
    loop=None,
    token=None
):
    from risk_manager import risk_manager

    # 1) pausa por rate limit
    if rate_limiter.is_paused():
        risk_manager.record(
            tipo="pair_skipped",
            mensagem="API rate limit pause",
            pair=pair_addr,
            token=None,
            origem=getattr(dex_info, "name", "DEX"),
            tx_hash=None,
            dry_run=config["DRY_RUN"]
        )
        safe_notify(bot, "⏸️ Sniper pausado por limite de API.", loop)
        return

    # 2) evita dupe
    now_ts = time()
    key = (pair_addr.lower(), token0.lower(), token1.lower())
    if key in _recent_pairs and (now_ts - _recent_pairs[key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Ignorando par: {pair_addr}")
        return
    _recent_pairs[key] = now_ts

    dex_name = getattr(dex_info, "name", "DEX")
    log.info(f"[Novo par] {dex_name} {pair_addr} {token0}/{token1}")
    risk_manager.record(
        tipo="pair_detected",
        mensagem="Novo par detectado",
        pair=pair_addr,
        token=None,
        origem=dex_name,
        tx_hash=None,
        dry_run=config["DRY_RUN"]
    )

    # 3) filtros iniciais
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        target = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth.lower()
            else Web3.to_checksum_address(token0)
        )

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            raise ValueError("TRADE_SIZE_ETH inválido")

        dex_client = DexClient(web3, getattr(dex_info, "router"))
        version = dex_client.detect_version(pair_addr)

        if version == DexVersion.V2:
            r0, r1 = dex_client._get_reserves(pair_addr)
            actual_liq = max(r0, r1)
        elif version == DexVersion.V3:
            actual_liq = dex_client._get_liquidity_v3(pair_addr)
        else:
            actual_liq = Decimal(0)

        MIN_LIQ = Decimal(str(config.get("MIN_LIQ_WETH", 0.5)))
        liq_ok = actual_liq >= MIN_LIQ

        # tenta obter preço
        price = dex_client.get_token_price(target, weth)
        if price is None:
            safe_notify(
                bot,
                f"⚠️ Preço indisponível para {target}; pulando par.",
                loop
            )
            return

        slip = dex_client.calc_dynamic_slippage(pair_addr, float(amt_eth))

        summary = (
            "🔍 *Novo Par Detectado*\n"
            f"• Endereço: `{pair_addr}`\n"
            f"• DEX: `{dex_name}`\n"
            f"• Versão: `{version.value}`\n"
            f"• Alvo: `{target}`\n"
            f"• Liquidez on-chain: `{actual_liq:.4f}` WETH (mín `{MIN_LIQ}`)\n"
            f"• Preço 1 token: `{price:.10f}` WETH\n"
            f"• Slippage sugerida: `{slip:.4f}`\n\n"
            "_Próximos filtros:_ liquidez → taxa → verificação → concentração"
        )
        safe_notify(bot, summary, loop)

        # filtro 1: liquidez
        if not liq_ok:
            risk_manager.record(
                tipo="pair_skipped",
                mensagem=f"liquidez on-chain {actual_liq:.4f} < {MIN_LIQ}",
                pair=pair_addr,
                token=target,
                origem="liq_check",
                tx_hash=None,
                dry_run=config["DRY_RUN"]
            )
            safe_notify(
                bot,
                (
                    f"⚠️ *Pool Ignorada:* liquidez on-chain `{actual_liq:.4f}` WETH "
                    f"< mínimo `{MIN_LIQ}` WETH\n"
                    "_Compra abortada_"
                ),
                loop
            )
            return

        # filtro 2: taxa
        exchange_for_tax = ExchangeClient(router_address=getattr(dex_info, "router"))
        MAX_TAX = float(config.get("MAX_TAX_PCT", 10.0))
        tax_ok = not has_high_tax(
            exchange_for_tax,
            target,
