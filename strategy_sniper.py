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

log = logging.getLogger("sniper")

# Instância global do RiskManager
risk_manager = RiskManager()

# Bot simples via token/chat_id
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])

# -----------------------------------------------
# Anti-duplicação de mensagens
# -----------------------------------------------
_last_msgs = {}
_DUP_INTERVAL = 5  # segundos

def notify(msg: str):
    """Envia mensagem simples ao Telegram."""
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
        log.error(f"Erro ao enviar notificação: {e}")

def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    """Envia mensagem ao Telegram evitando duplicação."""
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

# -----------------------------------------------
# Consulta de saldo
# -----------------------------------------------
def get_token_balance(web3: Web3, token_address: str, owner_address: str, erc20_abi: list) -> Decimal:
    """Consulta saldo de um token ERC20 em unidades humanas."""
    try:
        token = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)
        raw_balance = token.functions.balanceOf(Web3.to_checksum_address(owner_address)).call()
        decimals = token.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10 ** decimals)
    except Exception as e:
        log.error(f"Erro ao obter saldo do token {token_address}: {e}")
        return Decimal(0)

# -----------------------------------------------
# Filtros adicionais
# -----------------------------------------------
def has_high_tax(token_address: str, max_tax_pct: float = 10.0) -> bool:
    """Placeholder para verificar se token tem taxa acima do permitido."""
    try:
        return False
    except Exception as e:
        log.warning(f"Não foi possível verificar taxa do token {token_address}: {e}")
        return False

def has_min_volume(dex_client: DexClient, token_in: str, token_out: str, min_volume_eth: float) -> bool:
    """Verifica se o par tem volume >= min_volume_eth nas últimas transações."""
    try:
        volume_eth = dex_client.get_recent_volume(token_in, token_out)  # implementar no DexClient
        return volume_eth >= min_volume_eth
    except Exception as e:
        log.error(f"Erro ao verificar volume do par {token_in}/{token_out}: {e}")
        return False

def is_honeypot(token_address: str, router_address: str, weth_address: str, test_amount_eth: float) -> bool:
    """Simula uma venda para detectar honeypot."""
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        router = web3.eth.contract(address=Web3.to_checksum_address(router_address), abi=DEX_ROUTER_ABI)
        amount_out = router.functions.getAmountsOut(
            int(test_amount_eth * 10**18), [weth_address, token_address]
        ).call()
        return amount_out[1] == 0
    except Exception as e:
        log.warning(f"Falha no teste de honeypot ({token_address}): {e}")
        return True

# -----------------------------------------------
# Anti-duplicação de pares
# -----------------------------------------------
_recent_pairs = {}
_PAIR_DUP_INTERVAL = 5  # segundos

# -----------------------------------------------
# Fluxo principal de novo par
# -----------------------------------------------
async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
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
        if token0.lower() == weth.lower():
            target_token = Web3.to_checksum_address(token1)
        else:
            target_token = Web3.to_checksum_address(token0)

        amt_eth = Decimal(str(config.get("TRADE_SIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADE_SIZE_ETH inválido; abortando.")
            return

        # Checa liquidez mínima
        MIN_LIQ_WETH = config.get("MIN_LIQ_WETH", 0.5)
        if not DexClient(web3, dex_info["router"]).has_min_liquidity(target_token, weth, MIN_LIQ_WETH):
            safe_notify(bot, f"⚠️ Pool ignorada por liquidez insuficiente ({MIN_LIQ_WETH} WETH mín.)", loop)
            return

        # Checa volume mínimo
        MIN_VOLUME_ETH = config.get("MIN_VOLUME_ETH", 2.0)
        dex_client = DexClient(web3, dex_info["router"])
        if not has_min_volume(dex_client, weth, target_token, MIN_VOLUME_ETH):
            safe_notify(bot, f"⚠️ Pool ignorada por volume insuficiente (< {MIN_VOLUME_ETH} ETH)", loop)
            return

        # Teste de honeypot
        if is_honeypot(target_token, dex_info["router"], weth, 0.001):
            safe_notify(bot, f"🚫 Pool {target_token} bloqueada (possível honeypot)", loop)
            return

        # Checa taxa
        MAX_TAX_PCT = config.get("MAX_TAX_PCT", 10.0)
        if has_high_tax(target_token, MAX_TAX_PCT):
            safe_notify(bot, f"⚠️ Token ignorado por taxa acima de {MAX_TAX_PCT}%", loop)
            return

    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    preco_atual = dex_client.get_token_price(target_token, weth)
    log.info(f"[Pré-Risk] {token0}/{token1} preço={preco_atual} ETH | size={amt_eth}ETH")

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
        amount_out_min=None
    )

    if tx_buy:
        safe_notify(bot, f"✅ Compra realizada: {target_token}\nTX: {tx_buy}", loop)
    else:
        motivo = getattr(risk_manager, "last_block_reason", "não informado")
        safe_notify(bot, f"🚫 Compra não executada para {target_token}\nMotivo: {motivo}", loop)
        return

    # Monitoramento de venda
    highest_price = preco_atual
    trail_pct = config.get("TRAIL_PCT", 0.05)
    take_profit_price = preco_atual * (1 + config.get("TP_PCT", 0.2))
    entry_price = preco_atual
    stop_price = highest_price * (1 - trail_pct)
    sold = False

    from discovery import is_discovery_running
    while is
