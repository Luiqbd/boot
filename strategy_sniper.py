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
from risk_manager import RiskManager

# >>> NOVO: import para filtros + rate limiter
from utils import (
    is_contract_verified,
    is_token_concentrated,
    rate_limiter,
    configure_rate_limiter_from_config
)

log = logging.getLogger("sniper")

risk_manager = RiskManager()
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

API_KEY = config.get("BASESCAN_API_KEY")
BLOCK_UNVERIFIED = config.get("BLOCK_UNVERIFIED", False)
TOP_HOLDER_LIMIT = float(config.get("TOP_HOLDER_LIMIT", 30.0))

configure_rate_limiter_from_config(config)
rate_limiter.set_notifier(lambda msg: safe_notify(bot_notify, msg))

DEX_ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "outputs": [{"type": "uint256[]", "name": "amounts"}],
        "inputs": [
            {"type": "uint256", "name": "amountIn"},
            {"type": "address[]", "name": "path"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

_last_msgs = {}
_DUP_INTERVAL = 5

def notify(msg: str):
    try:
        coro = bot_notify.send_message(
            chat_id=config["TELEGRAM_CHAT_ID"],
            text=msg
        )
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception as e:
        log.error(f"Erro ao enviar notificação: {e}", exc_info=True)

def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    now = time()
    msg_key = hash(msg)
    if msg_key in _last_msgs and (now - _last_msgs[msg_key]) < _DUP_INTERVAL:
        log.debug(f"[DUPE] Mensagem ignorada: {msg}")
        return
    _last_msgs[msg_key] = now
    try:
        if alert:
            coro = alert.send_message(
                chat_id=config["TELEGRAM_CHAT_ID"],
                text=msg
            )
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                try:
                    running_loop = asyncio.get_running_loop()
                    running_loop.create_task(coro)
                except RuntimeError:
                    asyncio.run(coro)
        else:
            notify(msg)
    except Exception as e:
        log.error(f"Falha ao enviar alerta: {e}", exc_info=True)

def get_token_balance(web3: Web3, token_address: str, owner_address: str, erc20_abi: list) -> Decimal:
    try:
        token = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)
        raw_balance = token.functions.balanceOf(Web3.to_checksum_address(owner_address)).call()
        decimals = token.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10 ** decimals)
    except Exception as e:
        log.error(f"Erro ao obter saldo do token {token_address}: {e}", exc_info=True)
        return Decimal(0)

def has_high_tax(token_address: str, max_tax_pct: float = 10.0) -> bool:
    try:
        return False
    except Exception as e:
        log.warning(f"Não foi possível verificar taxa do token {token_address}: {e}")
        return False

def has_min_volume(dex_client: DexClient, token_in: str, token_out: str, min_volume_eth: float) -> bool:
    try:
        volume_eth = dex_client.get_recent_volume(token_in, token_out)
        return float(volume_eth) >= float(min_volume_eth)
    except Exception as e:
        log.error(f"Erro ao verificar volume do par {token_in}/{token_out}: {e}", exc_info=True)
        return False

def is_honeypot(token_address: str, router_address: str, weth_address: str, test_amount_eth: float, strict: bool = False) -> bool:
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        router = web3.eth.contract(address=Web3.to_checksum_address(router_address), abi=DEX_ROUTER_ABI)
        amount_in_wei = int(Decimal(str(test_amount_eth)) * (10 ** 18))
        amounts = router.functions.getAmountsOut(amount_in_wei, [weth_address, token_address]).call()
        return (len(amounts) < 2) or (int(amounts[-1]) == 0)
    except Exception as e:
        log.warning(f"Falha no teste de honeypot ({token_address}): {e}")
        return True if strict else False

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

        liq_ok = dex_client.has_min_liquidity(pair_addr, weth, MIN_LIQ_WETH)
        if not liq_ok:
            safe_notify(bot, f"⚠️ Pool ignorada por liquidez insuficiente (< {MIN_LIQ_WETH} WETH)", loop)
            return

        MAX_TAX_PCT = float(config.get("MAX_TAX_PCT", 10.0))
        if has_high_tax(target_token, MAX_TAX_PCT):
            safe_notify(bot, f"⚠️ Token ignorado por taxa acima de {MAX_TAX_PCT}%", loop)
            return

        if not is_contract_verified(target_token, API_KEY):
            safe_notify(bot, f"⚠️ Token {target_token} com contrato não verificado no BaseScan", loop)
            if BLOCK_UNVERIFIED:
                return

        if is_token_concentrated(target_token, API_KEY, TOP_HOLDER_LIMIT):
            safe_notify(bot, f"🚫 Token {target_token} com concentração alta de supply", loop)
            return

        preco_atual = dex_client.get_token_price(target_token, weth)
        slip_limit = dex_client.calc_dynamic_slippage(pair_addr, weth, float(amt_eth))
    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    log.info(f"[Pré-Risk] {token0}/{token1} preço={preco_atual} ETH | size={amt_eth} ETH | slippage={slip_limit*100:.2f}%")

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
        safe_notify(bot, f"✅ Compra realizada: {target_token}\nTX: {tx_buy}", loop)
    else:
        motivo = getattr(risk_manager, "last_block_reason", "não informado")
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
            except Exception as e:
                log.warning(f"Falha ao atualizar preço: {e}")
                await asyncio.sleep(1)
                continue

            if not price:
                await asyncio.sleep(1)
                continue

            if price > highest_price:
                highest_price = price
                stop_price = highest_price * (1 - trail_pct)

            should_sell = (price >= take_profit_price) or (price <= stop_price) or (price <= hard_stop_price)
            if should_sell:
                try:
                    token_balance = get_token_balance(web3, target_token, exchange_client.wallet, exchange_client.erc20_abi)
                except Exception as e:
                    log.error(f"Erro ao consultar saldo para venda: {e}", exc_info=True)
                    break

                if token_balance <= 0:
                    log.warning("Saldo do token é zero — nada para vender.")
                    break

                tx_sell = safe_exec.sell(
                    token_in=target_token,
                    token_out=weth,
                    amount_eth=token_balance,
                    current_price=price,
                    last_trade_price=entry_price
                )
                if tx_sell:
                    safe_notify(bot, f"💰 Venda realizada: {target_token}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = getattr(risk_manager, "last_block_reason", "não informado")
                    safe_notify(bot, f"⚠️ Venda bloqueada: {motivo}", loop)
                break

            await asyncio.sleep(int(config.get("INTERVAL", 3)))
    finally:
        if not sold and not is_discovery_running():
            safe_notify(bot, f"⏹ Monitoramento encerrado para {target_token} (sniper parado).", loop)

