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
from safetradeexecutor import SafeTradeExecutor
from risk_manager import RiskManager

>>> NOVO: import para filtros + rate limiter
from utils import (
    iscontractverified,
    istokenconcentrated,
    rate_limiter,
    configureratelimiterfromconfig
)

log = logging.getLogger("sniper")

risk_manager = RiskManager()
botnotify = Bot(token=config["TELEGRAMTOKEN"])

APIKEY = config.get("BASESCANAPI_KEY")
BLOCKUNVERIFIED = config.get("BLOCKUNVERIFIED", False)
TOPHOLDERLIMIT = float(config.get("TOPHOLDERLIMIT", 30.0))

configureratelimiterfromconfig(config)
ratelimiter.setnotifier(lambda msg: safenotify(botnotify, msg))

DEXROUTERABI = [
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

lastmsgs = {}
DUPINTERVAL = 5

def notify(msg: str):
    try:
        coro = botnotify.sendmessage(
            chatid=config["TELEGRAMCHAT_ID"],
            text=msg
        )
        try:
            loop = asyncio.getrunningloop()
            loop.create_task(coro)
        except RuntimeError:
            asyncio.run(coro)
    except Exception as e:
        log.error(f"Erro ao enviar notificação: {e}", exc_info=True)

def safe_notify(alert: TelegramAlert | None, msg: str, loop: asyncio.AbstractEventLoop | None = None):
    now = time()
    msg_key = hash(msg)
    if msgkey in lastmsgs and (now - lastmsgs[msgkey]) < DUPINTERVAL:
        log.debug(f"[DUPE] Mensagem ignorada: {msg}")
        return
    lastmsgs[msg_key] = now
    try:
        if alert:
            coro = alert.send_message(
                chatid=config["TELEGRAMCHAT_ID"],
                text=msg
            )
            if loop and loop.is_running():
                asyncio.runcoroutinethreadsafe(coro, loop)
            else:
                try:
                    runningloop = asyncio.getrunning_loop()
                    runningloop.createtask(coro)
                except RuntimeError:
                    asyncio.run(coro)
        else:
            notify(msg)
    except Exception as e:
        log.error(f"Falha ao enviar alerta: {e}", exc_info=True)

def gettokenbalance(web3: Web3, tokenaddress: str, owneraddress: str, erc20_abi: list) -> Decimal:
    try:
        token = web3.eth.contract(address=Web3.tochecksumaddress(tokenaddress), abi=erc20abi)
        rawbalance = token.functions.balanceOf(Web3.tochecksumaddress(owneraddress)).call()
        decimals = token.functions.decimals().call()
        return Decimal(raw_balance) / Decimal(10  decimals)
    except Exception as e:
        log.error(f"Erro ao obter saldo do token {tokenaddress}: {e}", excinfo=True)
        return Decimal(0)

def hashightax(tokenaddress: str, maxtax_pct: float = 10.0) -> bool:
    try:
        return False
    except Exception as e:
        log.warning(f"Não foi possível verificar taxa do token {token_address}: {e}")
        return False

def hasminvolume(dexclient: DexClient, tokenin: str, tokenout: str, minvolume_eth: float) -> bool:
    try:
        volumeeth = dexclient.getrecentvolume(tokenin, tokenout)
        return float(volumeeth) >= float(minvolume_eth)
    except Exception as e:
        log.error(f"Erro ao verificar volume do par {tokenin}/{tokenout}: {e}", exc_info=True)
        return False

def ishoneypot(tokenaddress: str, routeraddress: str, wethaddress: str, testamounteth: float, strict: bool = False) -> bool:
    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        router = web3.eth.contract(address=Web3.tochecksumaddress(routeraddress), abi=DEXROUTER_ABI)
        amountinwei = int(Decimal(str(testamounteth))  (10 * 18))
        amounts = router.functions.getAmountsOut(amountinwei, [wethaddress, tokenaddress]).call()
        return (len(amounts) < 2) or (int(amounts[-1]) == 0)
    except Exception as e:
        log.warning(f"Falha no teste de honeypot ({token_address}): {e}")
        return True if strict else False

recentpairs = {}
PAIRDUP_INTERVAL = 5

async def onnewpair(dexinfo, pairaddr, token0, token1, bot=None, loop=None):
    from utils import rate_limiter

    if ratelimiter.ispaused():
        safe_notify(bot, "⏸️ Sniper pausado por limite de API. Ignorando novos pares.", loop)
        return

    now = time()
    pairkey = (pairaddr.lower(), token0.lower(), token1.lower())
    if pairkey in recentpairs and (now - recentpairs[pairkey]) < PAIRDUP_INTERVAL:
        log.debug(f"[DUPE] Par ignorado: {pair_addr} {token0}/{token1}")
        return
    recentpairs[pair_key] = now

    log.info(f"Novo par recebido: {dexinfo['name']} {pairaddr} {token0}/{token1}")

    try:
        web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        weth = Web3.tochecksumaddress(config["WETH"])
        targettoken = Web3.tochecksumaddress(token1) if token0.lower() == weth.lower() else Web3.tochecksum_address(token0)

        amteth = Decimal(str(config.get("TRADESIZE_ETH", 0.1)))
        if amt_eth <= 0:
            log.error("TRADESIZEETH inválido; abortando.")
            return

        MINLIQWETH = float(config.get("MINLIQWETH", 0.5))
        dexclient = DexClient(web3, dexinfo["router"])

        liqok = dexclient.hasminliquidity(pairaddr, weth, MINLIQ_WETH)
        if not liq_ok:
            safenotify(bot, f"⚠️ Pool ignorada por liquidez insuficiente (< {MINLIQ_WETH} WETH)", loop)
            return

        MAXTAXPCT = float(config.get("MAXTAXPCT", 10.0))
        if hashightax(targettoken, MAXTAX_PCT):
            safenotify(bot, f"⚠️ Token ignorado por taxa acima de {MAXTAX_PCT}%", loop)
            return

        if not iscontractverified(targettoken, APIKEY):
            safenotify(bot, f"⚠️ Token {targettoken} com contrato não verificado no BaseScan", loop)
            if BLOCK_UNVERIFIED:
                return

        if istokenconcentrated(targettoken, APIKEY, TOPHOLDERLIMIT):
            safenotify(bot, f"🚫 Token {targettoken} com concentração alta de supply", loop)
            return

        precoatual = dexclient.gettokenprice(target_token, weth)
        sliplimit = dexclient.calcdynamicslippage(pairaddr, weth, float(amteth))
    except Exception as e:
        log.error(f"Falha ao preparar contexto do par: {e}", exc_info=True)
        return

    log.info(f"[Pré-Risk] {token0}/{token1} preço={precoatual} ETH | size={amteth} ETH | slippage={slip_limit*100:.2f}%")

    # --- Execução de compra ---
    try:
        exchangeclient = ExchangeClient(routeraddress=dex_info["router"])
        tradeexec = TradeExecutor(exchangeclient=exchangeclient, dryrun=config["DRY_RUN"])
        safeexec = SafeTradeExecutor(executor=tradeexec, riskmanager=riskmanager)
    except Exception as e:
        log.error(f"Falha ao criar ExchangeClient/Executor: {e}", exc_info=True)
        return

    txbuy = safeexec.buy(
        token_in=weth,
        tokenout=targettoken,
        amounteth=amteth,
        currentprice=precoatual,
        lasttradeprice=None,
        amountoutmin=None,
        slippage=slip_limit
    )

    if tx_buy:
        safenotify(bot, f"✅ Compra realizada: {targettoken}\nTX: {tx_buy}", loop)
    else:
        motivo = getattr(riskmanager, "lastblock_reason", "não informado")
        safenotify(bot, f"🚫 Compra não executada para {targettoken}\nMotivo: {motivo}", loop)
        return

--- Monitoramento de venda ---
    highestprice = precoatual
    trailpct = float(config.get("TRAILPCT", 0.05))
    tppct = float(config.get("TAKEPROFITPCT", config.get("TPPCT", 0.2)))
    slpct = float(config.get("STOPLOSS_PCT", 0.05))

    entryprice = precoatual
    takeprofitprice = entryprice * (1 + tppct)
    hardstopprice = entryprice * (1 - slpct)
    stopprice = highestprice * (1 - trail_pct)
    sold = False

    from discovery import isdiscoveryrunning
    try:
        while isdiscoveryrunning():
            try:
                price = dexclient.gettokenprice(targettoken, weth)
            except Exception as e:
                log.warning(f"Falha ao atualizar preço: {e}")
                await asyncio.sleep(1)
                continue

            if not price:
                await asyncio.sleep(1)
                continue

            if price > highest_price:
                highest_price = price
                stopprice = highestprice * (1 - trail_pct)

            shouldsell = (price >= takeprofitprice) or (price <= stopprice) or (price <= hardstopprice)
            if should_sell:
                try:
                    tokenbalance = gettokenbalance(web3, targettoken, exchangeclient.wallet, exchangeclient.erc20_abi)
                except Exception as e:
                    log.error(f"Erro ao consultar saldo para venda: {e}", exc_info=True)
                    break

                if token_balance <= 0:
                    log.warning("Saldo do token é zero — nada para vender.")
                    break

                txsell = safeexec.sell(
                    tokenin=targettoken,
                    token_out=weth,
                    amounteth=tokenbalance,
                    current_price=price,
                    lasttradeprice=entry_price
                )
                if tx_sell:
                    safenotify(bot, f"💰 Venda realizada: {targettoken}\nTX: {tx_sell}", loop)
                    sold = True
                else:
                    motivo = getattr(riskmanager, "lastblock_reason", "não informado")
                    safe_notify(bot, f"⚠️ Venda bloqueada: {motivo}", loop)
                break

            await asyncio.sleep(int(config.get("INTERVAL", 3)))
    finally:
        if not sold and not isdiscoveryrunning():
            safenotify(bot, f"⏹ Monitoramento encerrado para {targettoken} (sniper parado).", loop)
