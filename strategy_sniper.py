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
    configure_rate_limiter_from_config
)

log = logging.getLogger("sniper")

bot_notify = Bot(token=config["TELEGRAM_TOKEN"])
configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

_PAIR_DUP_INTERVAL = 5
_recent_pairs: dict[tuple[str, str, str], float] = {}

def notify(msg: str):
    coro = bot_notify.send_message(
        chat_id=config["TELEGRAM_CHAT_ID"],
        text=msg,
        parse_mode="MarkdownV2"
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
            text=msg,
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

async def on_new_pair(dex_info, pair_addr, token0, token1,
                      bot=None, loop=None):
    from risk_manager import risk_manager

    # pausa por rate limit
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

    # evita dupe
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

    try:
        # inicializa cliente e params
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
        version     = dex_client.detect_version(pair_addr)

        # pega liquidez on-chain
        if version == DexVersion.V2:
            r0, r1        = dex_client._get_reserves(pair_addr)
            actual_liq    = max(r0, r1)
        elif version == DexVersion.V3:
            actual_liq    = dex_client._get_liquidity_v3(pair_addr)
        else:
            actual_liq    = Decimal(0)

        MIN_LIQ = Decimal(str(config.get("MIN_LIQ_WETH", 0.5)))
        liq_ok  = actual_liq >= MIN_LIQ

        price  = dex_client.get_token_price(target, weth)
        slip   = dex_client.calc_dynamic_slippage(pair_addr, float(amt_eth))

        # notifica resumo e próximos passos
        safe_notify(
            bot,
            (
                f"🔍 *Novo Par Detectado*\n"
                f"• Endereço: `{pair_addr}`\n"
                f"• DEX: `{dex_name}`\n"
                f"• Versão: `{version.value}`\n"
                f"• Alvo: `{target}`\n"
                f"• Liquidez on-chain: `{actual_liq:.4f}` WETH (mín `{MIN_LIQ}`)\n"
                f"• Preço 1 token: `{price:.10f}` WETH\n"
                f"• Slippage sugerida: `{slip:.4f}`\n\n"
                f"_Próximos filtros:_ liquidez → taxa → verificação → concentração"
            ),
            loop
        )

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
                f"⚠️ *Pool Ignorada:* liquidez on-chain `{actual_liq:.4f}` WETH < mínimo `{MIN_LIQ}` WETH\n"
                f"_Compra abortada_",
                loop
            )
            return

        # filtro 2: taxa
        exchange_for_tax = ExchangeClient(router_address=getattr(dex_info, "router"))
        MAX_TAX  = float(config.get("MAX_TAX_PCT", 10.0))
        tax_ok   = not has_high_tax(
            exchange_for_tax,
            target,
            weth,
            sample_amount_wei=Web3.to_wei(amt_eth, "ether"),
            max_tax_bps=int(MAX_TAX * 100)
        )
        if not tax_ok:
            risk_manager.record(
                tipo="pair_skipped",
                mensagem=f"taxa > {MAX_TAX}%",
                pair=pair_addr,
                token=target,
                origem="tax_check",
                tx_hash=None,
                dry_run=config["DRY_RUN"]
            )
            safe_notify(
                bot,
                f"⚠️ *Token Ignorado:* taxa estimada > `{MAX_TAX}`%\n"
                f"_Compra abortada_",
                loop
            )
            return

        # filtro 3: verificação de contrato
        if config.get("BLOCK_UNVERIFIED", False) and not is_contract_verified(
            target, config.get("ETHERSCAN_API_KEY")
        ):
            risk_manager.record(
                tipo="pair_skipped",
                mensagem="contrato não verificado",
                pair=pair_addr,
                token=target,
                origem="verify_check",
                tx_hash=None,
                dry_run=config["DRY_RUN"]
            )
            safe_notify(
                bot,
                "🚫 *Token Bloqueado:* contrato não verificado\n"
                "_Compra abortada_",
                loop
            )
            return

        # filtro 4: concentração de tokens
        TOP_LIMIT = float(config.get("TOP_HOLDER_LIMIT", 30.0))
        if is_token_concentrated(target, TOP_LIMIT, config.get("ETHERSCAN_API_KEY")):
            risk_manager.record(
                tipo="pair_skipped",
                mensagem=f"concentração > {TOP_LIMIT}%",
                pair=pair_addr,
                token=target,
                origem="concentration_check",
                tx_hash=None,
                dry_run=config["DRY_RUN"]
            )
            safe_notify(
                bot,
                f"🚫 *Token Bloqueado:* concentração de holders > `{TOP_LIMIT}`%\n"
                "_Compra abortada_",
                loop
            )
            return

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Erro nos filtros iniciais: {e}", exc_info=True)
        risk_manager.record(
            tipo="error",
            mensagem=str(e),
            pair=pair_addr,
            token=target,
            origem="filter_setup",
            tx_hash=None,
            dry_run=config["DRY_RUN"]
        )
        safe_notify(
            bot,
            "*❌ Erro nos filtros iniciais:*\n"
            f"`{e}`\n\n"
            "_Traceback:_\n"
            f"```{tb}```",
            loop
        )
        return

    # 5) tentativa de compra
    risk_manager.record(
        tipo="buy_attempt",
        mensagem="tentativa de compra",
        pair=pair_addr,
        token=target,
        origem="buy_phase",
        tx_hash=None,
        dry_run=config["DRY_RUN"]
    )

# 6) setup e execução da compra
    try:
        exchange = ExchangeClient(router_address=getattr(dex_info, "router"))
        trade_exec = TradeExecutor(
            exchange_client=exchange,
            dry_run=config["DRY_RUN"]
        )
        safe_exec  = SafeTradeExecutor(
            executor=trade_exec,
            risk_manager=risk_manager
        )
    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Erro ao criar executor: {e}", exc_info=True)
        risk_manager.record(
            tipo="error",
            mensagem=str(e),
            pair=pair_addr,
            token=target,
            origem="exec_setup",
            tx_hash=None,
            dry_run=config["DRY_RUN"]
        )
        safe_notify(
            bot,
            (
                "*❌ Erro ao inicializar executor*\n"
                f"`{e}`\n\n"
                "_Traceback:_\n"
                f"```{tb}```"
            ),
            loop
        )
        return

    # 7) tentativa de compra
    try:
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
            risk_manager.record(
                tipo="buy_success",
                mensagem="compra realizada",
                pair=pair_addr,
                token=target,
                origem="buy_phase",
                tx_hash=tx_buy,
                dry_run=config["DRY_RUN"]
            )
            risk_manager.register_trade(
                success=True,
                pair=pair_addr,
                direction="buy",
                now_ts=int(time()),
            )
            safe_notify(
                bot,
                f"✅ *Compra realizada*\nToken: `{target}`\nTX: `{tx_buy}`",
                loop
            )
        else:
            motivo = risk_manager.last_block_reason or "não informado"
            risk_manager.record(
                tipo="buy_failed",
                mensagem=motivo,
                pair=pair_addr,
                token=target,
                origem="buy_phase",
                tx_hash=None,
                dry_run=config["DRY_RUN"]
            )
            risk_manager.register_trade(
                success=False,
                pair=pair_addr,
                direction="buy",
                now_ts=int(time()),
            )
            safe_notify(
                bot,
                f"🚫 *Compra falhou*\nMotivo: `{motivo}`",
                loop
            )
            return

    except Exception as e:
        tb = traceback.format_exc()
        log.error(f"Erro ao executar compra: {e}", exc_info=True)
        risk_manager.record(
            tipo="buy_failed",
            mensagem=str(e),
            pair=pair_addr,
            token=target,
            origem="buy_phase",
            tx_hash=None,
            dry_run=config["DRY_RUN"]
        )
        risk_manager.register_trade(
            success=False,
            pair=pair_addr,
            direction="buy",
            now_ts=int(time()),
        )
        safe_notify(
            bot,
            (
                "*🚫 Exceção na compra automática*\n"
                f"`{e}`\n\n"
                "_Traceback:_\n"
                f"```{tb}```"
            ),
            loop
        )
        return

    # 8) monitoramento para venda
    highest    = price
    entry      = price
    tp_pct     = float(config.get("TAKE_PROFIT_PCT", 0.2))
    sl_pct     = float(config.get("STOP_LOSS_PCT", 0.05))
    trail      = float(config.get("TRAIL_PCT", 0.05))
    tp_price   = entry * (1 + tp_pct)
    hard_stop  = entry * (1 - sl_pct)
    stop_price = highest * (1 - trail)
    sold       = False

    from discovery import is_discovery_running
    while is_discovery_running():
        try:
            price = dex_client.get_token_price(target, weth)
        except Exception:
            await asyncio.sleep(1)
            continue

        if price > highest:
            highest    = price
            stop_price = highest * (1 - trail)

        # condições de saída
        if price >= tp_price or price <= stop_price or price <= hard_stop:
            balance = get_token_balance(exchange, target)
            if balance <= 0:
                break

            try:
                tx_sell = safe_exec.sell(
                    token_in=target,
                    token_out=weth,
                    amount_eth=Decimal(str(balance)),
                    current_price=price,
                    last_trade_price=entry
                )
                if tx_sell:
                    risk_manager.record(
                        tipo="sell_success",
                        mensagem="venda realizada",
                        pair=pair_addr,
                        token=target,
                        origem="sell_phase",
                        tx_hash=tx_sell,
                        dry_run=config["DRY_RUN"]
                    )
                    risk_manager.register_trade(
                        success=True,
                        pair=pair_addr,
                        direction="sell",
                        now_ts=int(time()),
                    )
                    safe_notify(
                        bot,
                        f"💰 *Venda realizada*\nToken: `{target}`\nTX: `{tx_sell}`",
                        loop
                    )
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record(
                        tipo="sell_failed",
                        mensagem=motivo,
                        pair=pair_addr,
                        token=target,
                        origem="sell_phase",
                        tx_hash=None,
                        dry_run=config["DRY_RUN"]
                    )
                    risk_manager.register_trade(
                        success=False,
                        pair=pair_addr,
                        direction="sell",
                        now_ts=int(time()),
                    )
                    safe_notify(
                        bot,
                        f"⚠️ *Venda falhou*\nMotivo: `{motivo}`",
                        loop
                    )
            except Exception as e:
                tb = traceback.format_exc()
                log.error(f"Erro ao executar venda: {e}", exc_info=True)
                risk_manager.record(
                    tipo="sell_failed",
                    mensagem=str(e),
                    pair=pair_addr,
                    token=target,
                    origem="sell_phase",
                    tx_hash=None,
                    dry_run=config["DRY_RUN"]
                )
                risk_manager.register_trade(
                    success=False,
                    pair=pair_addr,
                    direction="sell",
                    now_ts=int(time()),
                )
                safe_notify(
                    bot,
                    (
                        "*⚠️ Exceção na venda automática*\n"
                        f"`{e}`\n\n"
                        "_Traceback:_\n"
                        f"```{tb}```"
                    ),
                    loop
                )
            break

        await asyncio.sleep(int(config.get("INTERVAL", 3)))

    if not sold:
        safe_notify(
            bot,
            f"⏹ *Monitoramento encerrou sem venda* para `{target}`",
            loop
        )
