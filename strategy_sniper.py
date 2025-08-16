import time, math, logging, datetime
from web3 import Web3
from eth_account import Account
from config import config
from exchange_client import ExchangeClient
from telegram_alert import TelegramAlert
from dex import DexClient  # ✅ Import necessário para validações

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sniper")

def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def amount_out_min(web3, router, amount_in_wei, path, slippage_bps):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
        "outputs": [{"name": "", "type": "uint256[]"}]
    }]
    r = web3.eth.contract(address=router, abi=router_abi)
    out = r.functions.getAmountsOut(amount_in_wei, path).call()[-1]
    return math.floor(out * (1 - slippage_bps / 10_000))

def get_token_price_in_weth(web3, router, token, weth):
    router_abi = [{
        "name": "getAmountsOut", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
        "outputs": [{"name": "", "type": "uint256[]"}]
    }]
    r = web3.eth.contract(address=router, abi=router_abi)
    amt_in = 10**18
    path = [token, weth]
    try:
        out = r.functions.getAmountsOut(amt_in, path).call()[-1]
        return out / 1e18
    except:
        return None

def on_new_pair(pair_addr, token0, token1, bot=None):
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    weth = Web3.to_checksum_address(config["WETH"])
    router = Web3.to_checksum_address(config["DEX_ROUTER"])
    alert = TelegramAlert(bot, config["TELEGRAM_CHAT_ID"]) if bot else None

    try:
        signer_addr = Account.from_key(config["PRIVATE_KEY"]).address
    except Exception:
        signer_addr = "<PRIVATE_KEY inválida ou ausente>"

    log.info(f"[{_now()}][autopsy] CHAIN_ID={config.get('CHAIN_ID')} RPC_URL={config.get('RPC_URL')}")
    log.info(f"[{_now()}][autopsy] DEX_ROUTER={router} WETH={weth} DRY_RUN={config.get('DRY_RUN')}")
    log.info(f"[{_now()}][autopsy] signer={signer_addr} TRADE_SIZE_ETH={config.get('TRADE_SIZE_ETH', 0.02)} SLIPPAGE_BPS={config.get('DEFAULT_SLIPPAGE_BPS')} DEADLINE_SEC={config.get('TX_DEADLINE_SEC')}")

    # Verifica se o contrato do router está implantado
    code = web3.eth.get_code(router)
    if code == b'0x':
        log.error(f"❌ Roteador {router} não está implantado na rede — swap abortado.")
        if alert:
            alert.send(f"❌ Roteador não implantado: {router}")
        return

    target_token = Web3.to_checksum_address(token1 if token0.lower() == weth.lower() else token0)
    log.info(f"🚀 Novo par aprovado — comprando {target_token} (pair {pair_addr})")
    if alert:
        alert.send(f"🚀 Novo par detectado: {target_token}\nPar: {pair_addr}")

    # Verifica honeypot e liquidez mínima
    dex = DexClient(web3)
    if dex.is_honeypot(target_token):
        log.warning(f"⚠️ Token {target_token} parece ser honeypot — swap abortado.")
        if alert:
            alert.send(f"⚠️ Token honeypot detectado: {target_token} — swap abortado.")
        return

    if not dex.has_min_liquidity(target_token):
        log.warning(f"⚠️ Token {target_token} tem liquidez insuficiente — swap abortado.")
        if alert:
            alert.send(f"⚠️ Liquidez insuficiente para {target_token} — swap abortado.")
        return

    amt_in = web3.to_wei(config.get("TRADE_SIZE_ETH", 0.02), "ether")
    path_buy = [weth, target_token]
    try:
        aout_min = amount_out_min(web3, router, amt_in, path_buy, config["DEFAULT_SLIPPAGE_BPS"])
        log.info(f"[{_now()}][autopsy] BUY preview via config router: path={path_buy} amount_in_wei={amt_in} min_out={aout_min}")
    except Exception as e:
        aout_min = None
        log.warning(f"[{_now()}][autopsy] Falha ao cotar getAmountsOut no router do config: {e}")

    deadline = int(time.time()) + config["TX_DEADLINE_SEC"]

    exch = ExchangeClient()
    try:
        internal_router = getattr(exch, "router").address
    except Exception:
        internal_router = "<indisponível>"
    try:
        rpc_used = getattr(getattr(exch, "web3"), "provider").endpoint_uri
    except Exception:
        rpc_used = "<indisponível>"
    log.info(f"[{_now()}][autopsy] ExchangeClient.router={internal_router} ExchangeClient.RPC={rpc_used}")

    # Verifica se o router interno é confiável
    if internal_router.lower() != router.lower():
        log.error(f"❌ Roteador interno ({internal_router}) difere do configurado ({router}) — swap abortado.")
        if alert:
            alert.send(f"❌ Roteador interno difere do configurado — swap abortado.\nConfig: {router}\nInterno: {internal_router}")
        return

    if config.get("DRY_RUN"):
        msg = (
            f"[{_now()}][DRY_RUN] Compra NÃO será enviada.\n"
            f" signer={signer_addr}\n"
            f" router_config={router}\n"
            f" router_exchange_client={internal_router}\n"
            f" path={path_buy}\n"
            f" amount_in_wei={amt_in}\n"
            f" min_out={aout_min}\n"
            f" deadline={deadline}"
        )
        log.warning(msg.replace("\n", " | "))
        if alert:
            alert.send("🧪 DRY_RUN ativo: compra NÃO será enviada.\n"
                       f"Router cfg: {router}\nRouter exch: {internal_router}\nAmountIn: {amt_in}\nMinOut: {aout_min}")
        return

    try:
        log.info(f"[{_now()}][buy] Enviando compra: amount_in_wei={amt_in} path={path_buy} deadline={deadline}")
        buy_tx = exch.buy_token(weth, target_token, amt_in)
        log.info(f"✅ Compra enviada — TX: {buy_tx}")
        if alert:
            alert.send(f"✅ Compra realizada: {target_token}\nTX: {buy_tx}")
    except Exception as e:
        log.error(f"❌ Falha na compra: {e}", exc_info=True)
        if alert:
            alert.send(f"❌ Falha na compra: {e}")
        return

    entry_price = get_token_price_in_weth(web3, router, target_token, weth)
    if not entry_price:
        log.warning("Não foi possível obter preço inicial")
        if alert:
            alert.send("⚠️ Não foi possível obter preço inicial do token.")
        return

    take_profit_price = entry_price * (1 + config.get("TAKE_PROFIT_PCT", 0.30))
    trail_pct = config.get("TRAIL_PCT", 0.10)
    highest_price = entry_price
    stop_price = entry_price * (1 - config.get("STOP_LOSS_PCT", 0.15))

    log.info(f"🎯 TP fixo: {take_profit_price:.6f} WETH | 🛑 SL inicial: {stop_price:.6f} WETH | 📈 Trailing: {trail_pct*100:.1f}%")
    if alert:
        alert.send(f"🎯 TP: {take_profit_price:.6f} WETH\n🛑 SL: {stop_price:.6f} WETH\n📈 Trailing: {trail_pct*100:.1f}%")

    while True:
        current_price = get_token_price_in_weth(web3, router, target_token, weth)
        if not current_price:
            time.sleep(1)
            continue

        if current_price > highest_price:
            highest_price = current_price
            stop_price = highest_price * (1 - trail_pct)
            log.info(f"📈 Novo topo: {highest_price:.6f} WETH | SL ajustado: {stop_price:.6f} WETH")
            if alert
