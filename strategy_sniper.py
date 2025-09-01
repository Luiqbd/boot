# strategy_sniper.py

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from web3 import Web3
from telegram import Bot
from telegram_alert import TelegramAlert

from config import config
from dex import DexClient
from exchange_client import ExchangeClient
from risk_manager import risk_manager
from safe_trade_executor import SafeTradeExecutor
from trade_executor import TradeExecutor
from utils import (
    configure_rate_limiter_from_config,
    rate_limiter,
    to_float,
    get_token_balance,
    is_contract_verified,
    is_token_concentrated,
    has_high_tax,
)

# Logger principal
log = logging.getLogger("sniper")
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

# Cache de tempo para evitar alertas duplicados
_last_alert_times: Dict[int, float] = {}
_ALERT_DEDUP_INTERVAL = to_float(config.get("PAIR_DUP_INTERVAL", 5))


def _enviar_telegram(mensagem: str) -> None:
    """
    Envia uma mensagem diretamente via Bot do Telegram.
    """
    tarefa = bot_notify.send_message(
        chat_id=config["TELEGRAM_CHAT_ID"],
        text=mensagem
    )
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(tarefa)
    except RuntimeError:
        # Caso não haja loop rodando, executa de forma síncrona
        asyncio.run(tarefa)


def safe_notify(
    alert: Optional[TelegramAlert],
    mensagem: str,
    loop: Optional[asyncio.AbstractEventLoop] = None
) -> None:
    """
    Dispara um alerta no Telegram com deduplicação de mensagens.
    Se um TelegramAlert for fornecido, usa-o; caso contrário, envia direto.
    """
    agora = time.time()
    chave = hash(mensagem)
    ultimo = _last_alert_times.get(chave, 0)

    # Se já mandamos esta mensagem há menos de _ALERT_DEDUP_INTERVAL segundos, ignoramos
    if agora - ultimo < _ALERT_DEDUP_INTERVAL:
        return

    _last_alert_times[chave] = agora

    if alert:
        tarefa = alert.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=mensagem
        )
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(tarefa, loop)
        else:
            try:
                asyncio.get_running_loop().create_task(tarefa)
            except RuntimeError:
                asyncio.run(tarefa)
    else:
        _enviar_telegram(mensagem)


# Configura o rate limiter a partir do config e aponta o notifier para safe_notify
configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(None, msg))

# Cache para pares recentes, evitando múltiplos disparos em curto intervalo
_recent_pairs: Dict[Tuple[str, str, str], float] = {}


async def on_new_pair(
    dex_info: Dict[str, Any],
    pair_address: str,
    token0: str,
    token1: str,
    alert: Optional[TelegramAlert] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """
    Fluxo principal para cada novo par detectado:
      1) Verifica se o rate limiter está pausado
      2) Ignora duplicatas locais recentes
      3) Checa liquidez mínima, token taxado, verificado e concentração
      4) Executa compra com SafeTradeExecutor
      5) Monitora preço para TP/SL/trailing-stop e executa venda
    """
    # 1) Rate limiter
    if rate_limiter.is_paused():
        risk_manager.record_event(
            "pair_skipped",
            reason="limite de API ativo",
            dex=dex_info["name"],
            pair=pair_address
        )
        safe_notify(alert, "⏸️ Sniper pausado: limite de API.", loop)
        return

    # 2) Ignora duplicatas locais
    agora = time.time()
    chave = (pair_address.lower(), token0.lower(), token1.lower())
    visto_em = _recent_pairs.get(chave, 0)
    if agora - visto_em < _ALERT_DEDUP_INTERVAL:
        log.debug(f"[DUPLICADO] Ignorado localmente: {pair_address}")
        return
    _recent_pairs[chave] = agora

    log.info(f"[NOVO PAR] {dex_info['name']} → {pair_address} ({token0}/{token1})")
    risk_manager.record_event("pair_detected", dex=dex_info["name"], pair=pair_address)

    # 3) Filtros on-chain
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        target = (
            Web3.to_checksum_address(token1)
            if token0.lower() == weth.lower()
            else Web3.to_checksum_address(token0)
        )

        amt_eth = Decimal(str(to_float(config.get("TRADE_SIZE_ETH", 0.1))))
        if amt_eth <= 0:
            raise ValueError("TRADE_SIZE_ETH precisa ser maior que zero")

        dex_client = DexClient(web3, dex_info["router"])

        # Checa liquidez mínima
        min_liq = to_float(config.get("MIN_LIQ_WETH", 0.5))
        if not dex_client.has_min_liquidity(pair_address, weth, min_liq):
            motivo = f"liquidez abaixo de {min_liq} WETH"
            risk_manager.record_event("pair_skipped", reason=motivo, pair=pair_address)
            safe_notify(alert, f"⚠️ Pool ignorada: {motivo}", loop)
            return

        # Preço e slippage dinâmico
        price = dex_client.get_token_price(target, weth)
        slip = dex_client.calc_dynamic_slippage(pair_address, weth, float(amt_eth))

        # Taxa alta?
        max_tax = to_float(config.get("MAX_TAX_PCT", 10.0))
        if has_high_tax(target, max_tax):
            motivo = f"taxa maior que {max_tax}%"
            risk_manager.record_event("pair_skipped", reason=motivo, pair=pair_address)
            safe_notify(alert, f"⚠️ Token ignorado: {motivo}", loop)
            return

        # Contrato verificado?
        if config.get("BLOCK_UNVERIFIED", False) and not is_contract_verified(target):
            motivo = "contrato não verificado"
            risk_manager.record_event("pair_skipped", reason=motivo, pair=pair_address)
            safe_notify(alert, f"🚫 Token bloqueado: {motivo}", loop)
            return

        # Concentração de holders
        top_limit = to_float(config.get("TOP_HOLDER_LIMIT", 30.0))
        if is_token_concentrated(target, top_limit_pct=top_limit):
            motivo = "alta concentração de supply"
            risk_manager.record_event("pair_skipped", reason=motivo, pair=pair_address)
            safe_notify(alert, f"🚫 Token bloqueado: {motivo}", loop)
            return

    except Exception as err:
        log.error("Erro ao preparar contexto on-chain", exc_info=True)
        risk_manager.record_event("error", reason=str(err), pair=pair_address)
        return

    # 4) Tentativa de compra
    risk_manager.record_event(
        "buy_attempt",
        token=target,
        amount_eth=float(amt_eth),
        price=float(price),
        slippage=float(slip),
    )
    exchange = ExchangeClient(router_address=dex_info["router"])
    executor = TradeExecutor(exchange, dry_run=config.get("DRY_RUN", False))
    safe_exec = SafeTradeExecutor(executor=executor, risk_manager=risk_manager)

    tx_buy = safe_exec.buy(
        token_in=weth,
        token_out=target,
        amount_eth=float(amt_eth),
        current_price=float(price),
        last_trade_price=None,
    )

    if not tx_buy:
        motivo = risk_manager.last_block_reason or "não informado"
        risk_manager.record_event("buy_failed", reason=motivo, token=target)
        risk_manager.register_trade(False, pnl_eth=0.0, direction="buy")
        safe_notify(alert, f"🚫 Compra falhou: {motivo}", loop)
        return

    # Compra sucedida
    risk_manager.record_event(
        "buy_success",
        token=target,
        amount_eth=float(amt_eth),
        price=float(price),
        tx_hash=tx_buy,
    )
    risk_manager.register_trade(True, pnl_eth=0.0, direction="buy")
    safe_notify(alert, f"✅ Compra realizada: {target}\nTX: {tx_buy}", loop)

    # 5) Monitoramento TP/SL/trail e venda
    entry_price = price
    highest_price = price
    tp_pct = to_float(config.get("TAKE_PROFIT_PCT", 0.2))
    sl_pct = to_float(config.get("STOP_LOSS_PCT", 0.05))
    trail_pct = to_float(config.get("TRAIL_PCT", 0.05))

    tp_price = entry_price * (1 + tp_pct)
    hard_stop = entry_price * (1 - sl_pct)
    stop_price = highest_price * (1 - trail_pct)

    sold = False
    from discovery import is_discovery_running

    try:
        while is_discovery_running():
            await asyncio.sleep(to_float(config.get("INTERVAL", 3)))
            try:
                price = dex_client.get_token_price(target, weth)
            except Exception:
                continue  # se falhar ao buscar preço, pula iteração

            # Atualiza trailing stop
            if price > highest_price:
                highest_price = price
                stop_price = highest_price * (1 - trail_pct)

            # Verifica condições de saída
            if price >= tp_price or price <= stop_price or price <= hard_stop:
                balance = get_token_balance(
                    web3,
                    token_address=target,
                    wallet_address=exchange.wallet,
                    abi=exchange.erc20_abi,
                )
                if balance <= 0:
                    break

                tx_sell = safe_exec.sell(
                    token_in=target,
                    token_out=weth,
                    amount_eth=float(balance),
                    current_price=price,
                    last_trade_price=entry_price,
                )

                if tx_sell:
                    risk_manager.record_event(
                        "sell_success",
                        token=target,
                        amount_eth=float(balance),
                        price=price,
                        tx_hash=tx_sell,
                    )
                    risk_manager.register_trade(True, pnl_eth=0.0, direction="sell")
                    safe_notify(alert, f"💰 Venda realizada: {target}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = risk_manager.last_block_reason or "não informado"
                    risk_manager.record_event("sell_failed", reason=motivo, token=target)
                    risk_manager.register_trade(False, pnl_eth=0.0, direction="sell")
                    safe_notify(alert, f"⚠️ Venda falhou: {motivo}", loop)

                break

    finally:
        if not sold:
            safe_notify(alert, f"⏹ Monitoramento encerrado: {target}", loop)
