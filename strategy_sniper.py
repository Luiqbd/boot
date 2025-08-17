import logging
import math
import datetime
import asyncio
from web3 import Web3
from eth_account import Account

from config import config
from telegram import Bot
from telegram_alert import TelegramAlert
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager

log = logging.getLogger("sniper")

# === Notificador direto pelo token/chat_id ===
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])
def notify(msg: str):
    try:
        bot_notify.send_message(chat_id=config["TELEGRAM_CHAT_ID"], text=msg)
    except Exception as e:
        log.error(f"Erro ao enviar notificação: {e}")

# === Envio seguro de mensagens (funciona com ou sem loop ativo) ===
def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    """
    Envia a mensagem via TelegramAlert (assíncrono) e também via notify() (sincrônico).
    - Se um loop for fornecido e estiver rodando, agenda thread-safe.
    - Caso estejamos dentro de um loop (get_running_loop), agenda via create_task.
    - Caso contrário, cria um loop novo com asyncio.run.
    """
    if alert:
        try:
            coro = alert._send_async(msg)  # usa o pipeline de chunk + retries do TelegramAlert
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                try:
                    running_loop = asyncio.get_running_loop()
                    running_loop.create_task(coro)
                except RuntimeError:
                    asyncio.run(coro)
        except Exception as e:
            log.error(f"Falha ao agendar envio para alerta Telegram: {e}", exc_info=True)
    # Envia também pelo notificador básico (chamada síncrona)
    try:
        notify(msg)
    except Exception as e:
        log.error(f"Falha no notify(): {e}", exc_info=True)

ROUTER_ABI = [{
    "name": "getAmountsOut",
    "type": "function",
    "stateMutability": "view",
    "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path", "type": "address[]"}
    ],
    "outputs": [{"name": "", "type": "uint256[]"}]
}]

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(router_contract, amount_in_wei, path, slippage_bps):
    out = router_contract.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(router_contract, token, weth):
    amt_in = 10 ** 18  # 1 token (em 18 decimais) para cotação inversa token->WETH
    path = [token, weth]
    try:
        out = router_contract.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18 if out > 0 else None
    except Exception as e:
        log.warning(f"Falha ao obter preço: {e}")
        return None

async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    """
    Handler principal para novo par detectado.
    - dex_info: dict com campos name, router, factory, type
    - pair_addr, token0, token1: endereços
    - bot: instância do Bot (opcional)
    - loop: event loop associado ao bot (opcional)
    """
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = config["WETH"]
    router_addr = dex_info["router"]

    router_contract = web3.eth.contract(address=router_addr, abi=ROUTER_ABI)
    alert = TelegramAlert(bot, config["TELEGRAM_CHAT_ID"], loop=loop) if bot else None

    try:
        signer_addr = Account.from_key(config["PRIVATE_KEY"]).address
    except Exception:
        signer_addr = "<PRIVATE_KEY inválida ou ausente>"

    log.info(f"[{_now()}] Novo par detectado — DEX={dex_info['name']} — CHAIN_ID={config.get('CHAIN_ID')}")
    log.info(f"Roteador={router_addr} WETH={weth} signer={signer_addr}")

    # Notificação inicial
    safe_notify(alert, f"🚀 Novo par detectado em {dex_info['name']}\nPair: {pair_addr}\nSigner: {signer_addr}", loop)

    # Sanidade do roteador
    if len(web3.eth.get_code(router_addr)) == 0:
        msg = f"❌ Roteador {router_addr} não implantado — abortando."
        log.error(msg)
        safe_notify(alert, msg, loop)
        return

    # Determina o token alvo (não-WETH)
    target_token = Web3.to_checksum_address(token1 if token0.lower() == weth.lower() else token0)
    safe_notify(alert, f"🚀 Par detectado\nPair: {pair_addr}", loop)

    # Cliente da DEX (por roteador específico)
    dex = DexClient(web3, router_addr)

    # Segurança: honeypot
    if dex.is_honeypot(target_token, weth):
        warn = f"⚠️ Token {target_token} parece honeypot — abortando."
        log.warning(warn)
        safe_notify(alert, warn, loop)
        return

    # Checagem de liquidez mínima
    if not dex.has_min_liquidity(target_token, weth, min_liq_weth=config.get("MIN_LIQ_WETH", 0.5)):
        warn = f"⚠️ Liquidez insuficiente para {target_token} — abortando."
        log.warning(warn)
        safe_notify(alert, warn, loop)
        return

    # Tamanho da ordem
    amt_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
    if amt_eth <= 0:
        msg = "❌ TRADE_SIZE_ETH inválido — abortando."
        log.error(msg)
        safe_notify(alert, msg, loop)
        return
    amt_in = web3.to_wei(amt_eth, "ether")

    # Slippage padronizado
    try:
        slippage_bps = config.get("SLIPPAGE_BPS", config.get("DEFAULT_SLIPPAGE_BPS", 100))
        aout_min = amount_out_min(router_contract, amt_in, [weth, target_token], slippage_bps)
    except Exception as e:
        log.warning(f"Falha ao calcular minOut: {e}")
        aout_min = None

    # Infra de execução
    exch_client = ExchangeClient()
    trade_exec = TradeExecutor(exch_client, dry_run=config.get("DRY_RUN", True))
    risk_mgr = RiskManager(
        capital_eth=config.get("CAPITAL_ETH", 1.0),
        max_exposure_pct=config.get("MAX_EXPOSURE_PCT", 0.1),
        max_trades_per_day=config.get("MAX_TRADES_PER_DAY", 10),
        loss_limit=config.get("LOSS_LIMIT", 3),
        loss_pct_limit=config.get("LOSS_PCT_LIMIT", 0.15),
        daily_loss_pct_limit=config.get("DAILY_LOSS_PCT_LIMIT", 0.15),
        cooldown_sec=config.get("COOLDOWN_SEC", 30)
    )
    safe_exec = SafeTradeExecutor(trade_exec, risk_mgr, dex)

    # Preço atual
    current_price = get_token_price_in_weth(router_contract, target_token, weth)
    if current_price is None or current_price <= 0:
        msg = f"⚠️ Preço inválido para {target_token}, abortando."
        log.error(msg)
        safe_notify(alert, msg, loop)
        return

    # Modo simulado
    if config.get("DRY_RUN"):
        msg = f"🧪 DRY_RUN: Compra simulada {target_token}, min_out={aout_min}"
        log.warning(msg)
        safe_notify(alert, msg, loop)
        return

    # Execução de compra (protegida)
    tx = safe_exec.buy(weth, target_token, amt_eth, current_price, None)
    if tx:
        msg = f"✅ Compra realizada: {target_token}\nTX: {tx}"
        log.info(f"✅ Compra executada — TX: {tx}")
        safe_notify(alert, msg, loop)
    else:
        warn = f"⚠️ Compra bloqueada pelo RiskManager: {target_token}"
        log.warning(warn)
        safe_notify(alert, warn, loop)
        return

    # Gestão de posição: TP, SL e trailing
    entry_price = current_price
    take_profit_price = entry_price * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0.10)
    highest_price = entry_price
    stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    tp_sl_msg = (
        f"🎯 TP: {take_profit_price:.6f} WETH\n"
        f"🛑 SL: {stop_price:.6f} WETH\n"
        f"📈 Trailing: {trail_pct*100:.1f}%"
    )
    safe_notify(alert, tp_sl_msg, loop)

    # Loop de monitoramento com escape se sniper parar
    from discovery import is_discovery_running
    sold = False
    while is_discovery_running():
        price = get_token_price_in_weth(router_contract, target_token, weth)
        if not price:
            await asyncio.sleep(1)
            continue

        if price > highest_price:
            highest_price = price
            stop_price = highest_price * (1 - trail_pct)

        if price >= take_profit_price or price <= stop_price:
            sell_tx = safe_exec.sell(target_token, weth, amt_eth, price, entry_price)
            if sell_tx:
                msg = f"💰 Venda realizada: {target_token}\nTX: {sell_tx}"
                log.info(f"💰 Venda executada — TX: {sell_tx}")
                safe_notify(alert, msg, loop)
                sold = True
            else:
                warn = f"⚠️ Venda bloqueada pelo RiskManager: {target_token}"
                log.warning(warn)
                safe_notify(alert, warn, loop)
            break

        await asyncio.sleep(3)

    # Caso o sniper seja parado antes da venda
    if not sold and not is_discovery_running():
        msg = f"⏹ Monitoramento encerrado para {target_token} (sniper parado)."
        log.info(msg)
        safe_notify(alert, msg, loop)
