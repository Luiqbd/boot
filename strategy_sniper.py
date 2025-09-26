# strategy_sniper.py

import asyncio
import logging
import traceback
from decimal import Decimal
from time import time
from typing import Tuple

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
from telegram import Bot

from config import config
from telegram_alert import TelegramAlert
from dex import DexClient, DexVersion
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import risk_manager
from discovery import subscribe_new_pairs, is_discovery_running
from utils import (
    escape_md_v2,
    get_token_balance,
    has_high_tax,
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config,
)

log = logging.getLogger("sniper")
configure_rate_limiter_from_config(config)

# Cliente Telegram (bot + alert) e chat ID
BOT = Bot(token=config["TELEGRAM_TOKEN"])
ALERT = TelegramAlert(
    bot=BOT,
    chat_id=config["TELEGRAM_CHAT_ID"]
)
CHAT = config["TELEGRAM_CHAT_ID"]

# Intervalos para dedupe de pares e mensagens
PAIR_DUP_INTERVAL = float(config.get("PAIR_DUP_INTERVAL", 5))
MSG_DUP_INTERVAL = PAIR_DUP_INTERVAL * 0.5

# Histórico de pares processados recentemente
_processed_pairs: dict[Tuple[str, str, str], float] = {}


def _notify(texto: str, via_alert: bool = False):
    """
    Envia mensagem escapando MarkdownV2 e fazendo dedupe.
    via_alert=True usa TelegramAlert.send, senão Bot.send_message.
    """
    agora = time()
    chave = hash(texto)
    last = getattr(_notify, "_last_msgs", {})
    if last.get(chave, 0) + MSG_DUP_INTERVAL > agora:
        return
    last[chave] = agora
    _notify._last_msgs = last

    if via_alert:
        ALERT.send(texto)
        return

    txt = escape_md_v2(texto)
    coro = BOT.send_message(chat_id=CHAT, text=txt, parse_mode="MarkdownV2")
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


class StrategySniper:
    def __init__(self):
        # Web3 + WETH
        prov = Web3.HTTPProvider(config["RPC_URL"])
        self.w3 = Web3(prov)
        # corrigido: usa to_checksum_address
        self.weth = Web3.to_checksum_address(config["WETH"])

        # Parâmetros de configuração
        self.trade_size = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        self.min_liq = Decimal(str(config.get("MIN_LIQ_WETH", 0.5)))
        self.max_tax_bps = int(float(config.get("MAX_TAX_PCT", 10.0)) * 100)
        self.top_holder_limit = float(config.get("TOP_HOLDER_LIMIT", 30.0))
        self.tp_pct = float(config.get("TAKE_PROFIT_PCT", 0.2))
        self.sl_pct = float(config.get("STOP_LOSS_PCT", 0.05))
        self.trail_pct = float(config.get("TRAIL_PCT", 0.05))
        self.interval = int(config.get("INTERVAL", 3))

    async def on_new_pair(self, dex_info, pair: str, t0: str, t1: str):
        nome = getattr(dex_info, "name", "DEX")
        key = (pair.lower(), t0.lower(), t1.lower())
        agora = time()

        # 1) pausa por rate limit
        if rate_limiter.is_paused():
            risk_manager.record(
                "pair_skipped", "API pausada",
                pair, None, nome, None, config["DRY_RUN"]
            )
            _notify("⏸️ Sniper pausado por limite de API.", via_alert=True)
            return

        # 2) dedupe de pares
        last = _processed_pairs.get(key, 0)
        if agora - last < PAIR_DUP_INTERVAL:
            log.debug(f"[DUPLICADO] pulando {pair}")
            return
        _processed_pairs[key] = agora

        log.info(f"[NOVO PAR] {nome} {pair} {t0}/{t1}")
        risk_manager.record(
            "pair_detected", "Novo par detectado",
            pair, None, nome, None, config["DRY_RUN"]
        )

        # 3) configuração inicial e filtros
        try:
            base, target = self._identificar_tokens(t0, t1)
            dex = DexClient(self.w3, dex_info.router)
            versao = dex.detect_version(pair)

            # 3.1) liquidez
            liq = (
                max(*dex._get_reserves(pair))
                if versao == DexVersion.V2
                else dex._get_liquidity_v3(pair)
            )
            if liq < self.min_liq:
                msg = f"Liquidez {liq:.4f} < mínimo {self.min_liq}"
                risk_manager.record(
                    "pair_skipped", msg,
                    pair, target, "liq_check", None, config["DRY_RUN"]
                )
                _notify(f"⚠️ Skip liquidez: {msg}", via_alert=True)
                return

            # 3.2) preço e slippage
            preco = dex.get_token_price(target, base)
            if preco is None:
                _notify(f"⚠️ Preço indisponível para {target}", via_alert=True)
                return
            slip = dex.calc_dynamic_slippage(pair, float(self.trade_size))

            # 3.3) notifica resumo
            resumo = (
                "🔍 *Novo Par Detectado*\n"
                f"• Pair: `{pair}`\n"
                f"• DEX: `{nome}`\n"
                f"• Versão: `{versao.value}`\n"
                f"• Alvo: `{target}`\n"
                f"• Liquidez: `{liq:.4f}` WETH\n"
                f"• Preço: `{preco:.10f}` WETH\n"
                f"• Slippage: `{slip:.4f}`\n\n"
                "_Próximos filtros:_ taxa → verificação → concentração"
            )
            _notify(resumo, via_alert=True)

            # 3.4) taxa
            exch = ExchangeClient(router_address=dex_info.router)
            if has_high_tax(
                exch, target, base,
                self.w3.toWei(self.trade_size, "ether"),
                self.max_tax_bps
            ):
                risk_manager.record(
                    "pair_skipped", "Taxa alta",
                    pair, target, "tax_check", None, config["DRY_RUN"]
                )
                _notify(f"⚠️ Skip taxa > {self.max_tax_bps/100:.1f}%", via_alert=True)
                return

            # 3.5) contrato verificado
            if config.get("BLOCK_UNVERIFIED", False) and not is_contract_verified(
                target, config["ETHERSCAN_API_KEY"]
            ):
                risk_manager.record(
                    "pair_skipped", "Contrato não verificado",
                    pair, target, "verify_check", None, config["DRY_RUN"]
                )
                _notify("🚫 Skip contrato não verificado", via_alert=True)
                return

            # 3.6) concentração de holders
            if is_token_concentrated(
                target, self.top_holder_limit, config["ETHERSCAN_API_KEY"]
            ):
                msg = f"Concentração > {self.top_holder_limit:.1f}%"
                risk_manager.record(
                    "pair_skipped", msg,
                    pair, target, "concentration_check", None, config["DRY_RUN"]
                )
                _notify(f"🚫 Skip {msg}", via_alert=True)
                return

        except Exception as e:
            tb = traceback.format_exc()
            log.error(f"Erro filtros iniciais: {e}", exc_info=True)
            risk_manager.record(
                "error", str(e),
                pair, target, "filter_setup", None, config["DRY_RUN"]
            )
            _notify(f"*❌ Erro filtros:* `{e}`\n```{tb}```", via_alert=True)
            return

        # 4) tentativa de compra
        try:
            risk_manager.record(
                "buy_attempt", "Tentativa de compra",
                pair, target, "buy_phase", None, config["DRY_RUN"]
            )
            exch2 = ExchangeClient(router_address=dex_info.router)
            te = TradeExecutor(exchange_client=exch2, dry_run=config["DRY_RUN"])
            safe = SafeTradeExecutor(executor=te, risk_manager=risk_manager)

            tx_hash = safe.buy(
                token_in=base,
                token_out=target,
                amount_eth=self.trade_size,
                current_price=preco,
                last_trade_price=None,
                amount_out_min=None,
                slippage=slip
            )
            if not tx_hash:
                motivo = risk_manager.last_block_reason or "Desconhecido"
                risk_manager.record(
                    "buy_failed", motivo,
                    pair, target, "buy_phase", None, config["DRY_RUN"]
                )
                _notify(f"🚫 Compra falhou: {motivo}", via_alert=True)
                return

            risk_manager.record(
                "buy_success", "Compra realizada",
                pair, target, "buy_phase", tx_hash, config["DRY_RUN"]
            )
            risk_manager.register_trade(True, pair, "buy", int(time()))
            _notify(f"✅ Compra OK\nToken: `{target}`\nTX: `{tx_hash}`", via_alert=True)

        except Exception as e:
            tb = traceback.format_exc()
            log.error(f"Erro na compra: {e}", exc_info=True)
            risk_manager.record(
                "buy_failed", str(e),
                pair, target, "buy_phase", None, config["DRY_RUN"]
            )
            _notify(f"*🚫 Exceção compra:* `{e}`\n```{tb}```", via_alert=True)
            return

        # 5) monitoramento para venda
        await self._monitorar_venda(dex, pair, base, target, preco)

    def _identificar_tokens(self, t0: str, t1: str) -> Tuple[str, str]:
        """Retorna (base=WETH, target) em checksum."""
        # corrigido: usa to_checksum_address
        c0 = Web3.to_checksum_address(t0)
        c1 = Web3.to_checksum_address(t1)
        return (self.weth, c1) if c0 == self.weth else (self.weth, c0)

    async def _monitorar_venda(
        self,
        dex: DexClient,
        pair: str,
        base: str,
        target: str,
        entry: Decimal
    ):
        """
        Aguarda condições de take-profit/trailing/stop-loss e vende.
        """
        highest = entry
        tp_price = entry * (1 + self.tp_pct)
        hard_stop = entry * (1 - self.sl_pct)
        stop_price = highest * (1 - self.trail_pct)
        sold = False

        while is_discovery_running():
            await asyncio.sleep(self.interval)
            preco_atual = dex.get_token_price(target, base)
            if preco_atual is None:
                continue

            if preco_atual > highest:
                highest = preco_atual
                stop_price = highest * (1 - self.trail_pct)

            if (
                preco_atual >= tp_price
                or preco_atual <= stop_price
                or preco_atual <= hard_stop
            ):
                bal = get_token_balance(
                    ExchangeClient(router_address=dex.router),
                    target
                )
                if bal <= 0:
                    break

                try:
                    exch3 = ExchangeClient(router_address=dex.router)
                    te2 = TradeExecutor(exchange_client=exch3, dry_run=config["DRY_RUN"])
                    safe2 = SafeTradeExecutor(executor=te2, risk_manager=risk_manager)

                    tx_s = safe2.sell(
                        token_in=target,
                        token_out=base,
                        amount_eth=Decimal(str(bal)),
                        current_price=preco_atual,
                        last_trade_price=entry
                    )
                    if tx_s:
                        risk_manager.record(
                            "sell_success", "Venda realizada",
                            pair, target, "sell_phase", tx_s, config["DRY_RUN"]
                        )
                        risk_manager.register_trade(True, pair, "sell", int(time()))
                        _notify(f"💰 Venda OK\nToken: `{target}`\nTX: `{tx_s}`", via_alert=True)
                        sold = True
                    else:
                        motivo = risk_manager.last_block_reason or "Desconhecido"
                        risk_manager.record(
                            "sell_failed", motivo,
                            pair, target, "sell_phase", None, config["DRY_RUN"]
                        )
                        _notify(f"⚠️ Venda falhou: {motivo}", via_alert=True)
                except Exception as e:
                    tb = traceback.format_exc()
                    log.error(f"Erro na venda: {e}", exc_info=True)
                    risk_manager.record(
                        "sell_failed", str(e),
                        pair, target, "sell_phase", None, config["DRY_RUN"]
                    )
                    _notify(f"*⚠️ Exceção venda:* `{e}`\n```{tb}```", via_alert=True)
                break

        if not sold:
            _notify(f"⏹ Monitoramento encerrado sem venda para `{target}`", via_alert=True)


# ─── Instância global e exportação ─────────────────────────
_sniper = StrategySniper()
on_new_pair = _sniper.on_new_pair

# ─── Entrypoint standalone ──────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    subscribe_new_pairs(on_new_pair)
