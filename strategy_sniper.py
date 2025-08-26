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
from risk_manager import risk_manager

# >>> NOVO: import para filtros + rate limiter
from utils import (
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config
)

log = logging.getLogger("sniper")
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

# … (funções notify, safe_notify, get_token_balance, has_high_tax, has_min_volume, is_honeypot)

_recent_pairs = {}
_PAIR_DUP_INTERVAL = 5

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    from utils import rate_limiter

    if rate_limiter.is_paused():
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando novos pares.", loop)
        return

    now = time()
    pair_key = (pair_addr.lower(), token0.lower(), token1.lower())
    if pair_key in _recent_pairs and (now - _recent_pairs[pair_key]) < _PAIR_DUP_INTERVAL:
        log.debug(f"[DUPE] Par ignorado: {pair_addr} {token0}/{token1}")
        return
    _recent_pairs[pair_key] = now

    log.info(f"Novo par recebido: {dex_info['name']} {pair_addr} {token0}/{token1}")
    risk_manager.record_event(
        "pair_detected",
        dex=dex_info["name"],
        pair=pair_addr,
        token_in=token0,
        token_out=token1,
        time=now
    )

    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.to_checksum_address(config["WETH"])
        target_token = Web3.to_checksum_address(token1) if token0.lower() == weth.lower() else Web3.to_checksum_address(token0)
        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADE_SIZE_ETH inválido; abortando.")
            return

        MIN_LIQ_WETH = float(config.get("MIN_LIQ_WETH", 0.5))
        dex_client = DexClient(web3, dex_info["router"])
        if not dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ_WETH):
            safe_notify(bot, f"⚠️ Pool ignorada por liquidez insuficiente (< {MIN_LIQ_WETH} WETH)", loop)
            return

        MAX_TAX_PCT = float(config.get("MAX_TAX_PCT", 10.0))
        if has_high_tax(target_token, MAX_TAX_PCT):
            safe_notify(bot, f"⚠️ Token ignorado por taxa acima de {MAX_TAX_PCT}%", loop)
            return

        if not is_contract_verified(target_token, config.get("ETHERSCAN_API_KEY")) and config.get("BLOCK_UNVERIFIED", False):
            safe_notify(bot, f"⚠️ Token {target_token} não verificado; bloqueado.", loop)
            return

        if is_token_concentrated(target_token, config.get("ETHERSCAN_API_KEY"), float(config.get("TOP_HOLDER_LIMIT", 30.0))):
            safe_notify(bot, f"🚫 Token {target_token} com concentração alta de supply", loop)
            return

        preco_atual = dex_client.get_token_price(target_token, weth)
        slip_limit = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))

    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    # --- Execução de compra ---
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
        amount_out_min=None,
        slippage=slip_limit
    )

    if tx_buy:
        risk_manager.record_event(
            "buy_success",
            token=target_token,
            amount_eth=float(amt_eth),
            price=float(preco_atual),
            tx_hash=tx_buy,
            time=time()
        )
        safe_notify(bot, f"✅ Compra realizada: {target_token}\nTX: {tx_buy}", loop)
    else:
        motivo = getattr(risk_manager, "last_block_reason", "não informado")
        risk_manager.record_event(
            "buy_failed",
            token=target_token,
            reason=motivo,
            time=time()
        )
        safe_notify(bot, f"🚫 Compra não executada para {target_token}\nMotivo: {motivo}", loop)
        return

    # --- Monitoramento de venda ---
    highest_price = preco_atual
    trail_pct = float(config.get("TRAIL_PCT", 0.05))
    tp_pct = float(config.get("TAKE_PROFIT_PCT", config.get("TP_PCT", 0.2)))
    sl_pct = float(config.get("STOP_LOSS_PCT", 0.05))

    entry_price = preco_atual
    take_profit_price = entry_price * (1 + tp_pct)
    hard_stop_price = entry_price * (1 - sl_pct)
    stop_price = highest_price * (1 - trail_pct)
    sold = False

    from discovery import is_discovery_running
    try:
        while is_discovery_running():
            try:
                price = dex_client.get_token_price(target_token, weth)
            except Exception:
                await asyncio.sleep(1)
                continue

            if price > highest_price:
                highest_price = price
                stop_price = highest_price * (1 - trail_pct)

            should_sell = (
                price >= take_profit_price or
                price <= stop_price or
                price <= hard_stop_price
            )

            if should_sell:
                token_balance = get_token_balance(
                    web3, target_token,
                    exchange_client.wallet,
                    exchange_client.erc20_abi
                )
                if token_balance <= 0:
                    log.warning("Saldo do token é zero — nada para vender.")
                    break

                tx_sell = safe_exec.sell(
                    token_in=target_token,
                    token_out=weth,
                    amount_eth=float(token_balance),
                    current_price=price,
                    last_trade_price=entry_price
                )
                if tx_sell:
                    risk_manager.record_event(
                        "sell_success",
                        token=target_token,
                        amount=float(token_balance),
                        price=float(price),
                        tx_hash=tx_sell,
                        time=time()
                    )
                    safe_notify(bot, f"💰 Venda realizada: {target_token}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = getattr(risk_manager, "last_block_reason", "não informado")
                    risk_manager.record_event(
                        "sell_failed",
                        token=target_token,
                        reason=motivo,
                        time=time()
                    )
                    safe_notify(bot, f"⚠️ Venda bloqueada: {motivo}", loop)
                break

            await asyncio.sleep(int(config.get("INTERVAL", 3)))
    finally:
        if not sold:
            safe_notify(bot, f"⏹ Monitoramento encerrado para {target_token}.", loop)

# risk_manager.py

class RiskManager:
    def __init__(self):
        self.events = []
        self.last_block_reason = None

    def record_event(self, event_type: str, **data):
        # guarda última razão de bloqueio se houver
        if "reason" in data:
            self.last_block_reason = data["reason"]
        self.events.append({"type": event_type, **data})

    def generate_report(self) -> str:
        total_pairs = sum(1 for e in self.events if e["type"] == "pair_detected")
        buys_ok = [e for e in self.events if e["type"] == "buy_success"]
        buys_fail = [e for e in self.events if e["type"] == "buy_failed"]
        sells_ok = [e for e in self.events if e["type"] == "sell_success"]
        sells_fail = [e for e in self.events if e["type"] == "sell_failed"]

        lines = [
            "📊 Relatório de Eventos",
            f"- Pares detectados: {total_pairs}",
            f"- Compras realizadas: {len(buys_ok)}",
            f"- Compras bloqueadas: {len(buys_fail)}",
            f"- Vendas realizadas: {len(sells_ok)}",
            f"- Vendas bloqueadas: {len(sells_fail)}",
            "",
            "🛑 Razões de bloqueio de compra:"
        ]
        for e in buys_fail:
            lines.append(f"  • {e['token']} → {e['reason']}")

        lines.append("\n🛑 Razões de bloqueio de venda:")
        for e in sells_fail:
            lines.append(f"  • {e['token']} → {e['reason']}")

        return "\n".join(lines)

# instância global
risk_manager = RiskManager()
