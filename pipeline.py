import asyncio
from typing import Any
from decimal import Decimal
from web3 import Web3

from config import config
from classifier import should_buy
from trading import buy
from storage import add_position
from dex_client import DexClient
from metrics import BUY_ATTEMPTS, BUY_SUCCESSES, ERRORS
from notifier import send

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def on_pair(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: Any
) -> None:
    BUY_ATTEMPTS.inc()

    aprovado = await should_buy(pair_addr, token0, token1, dex_info)
    token = token1 if token0.lower() == WETH.lower() else token0
    if not aprovado:
        send(f"🚫 Token honeypot detectado: {token} — desconsiderando par {pair_addr}")
        return

    send(f"✅ Par aprovado: {pair_addr} → token {token} elegível para compra")
    send(f"💰 Tentando comprar {config['TRADE_SIZE_ETH']} WETH → {token} no par {pair_addr}")

    tx_hash = await buy(amount_in_wei=int(Decimal(config['TRADE_SIZE_ETH'])*10**18), token_out=token)
    if not tx_hash:
        return

    BUY_SUCCESSES.inc()
    price = DexClient(Web3(Web3.HTTPProvider(RPC_URL)), dex_info.router) \
        .get_token_price(token_address=token, weth_address=WETH) or 0.0

    add_position(pair=token, amount=int(Decimal(config['TRADE_SIZE_ETH'])*10**18), avg_price=price)

    send(
        "✅ Compra executada:\n"
        f"• Token: {token}\n"
        f"• Par: {pair_addr}\n"
        f"• TX: {tx_hash}\n"
        f"• Preço de entrada: {price:.6f} WETH"
    )
