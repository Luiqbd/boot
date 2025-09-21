# pipeline.py

import asyncio
from typing import Any
from decimal import Decimal

from config import config
from classifier import should_buy
from trading import buy
from storage import add_position
from dex_client import DexClient
from metrics import BUY_ATTEMPTS, BUY_SUCCESSES, ERRORS, OPEN_POSITIONS
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
    token = token1 if token0.lower() == WETH.lower() else token0
    try:
        # Filtro de honeypot
        aprovado = await should_buy(pair_addr, token0, token1, dex_info)
        if not aprovado:
            send(f"🚫 Token honeypot detectado: {token} — par {pair_addr} ignorado")
            return

        send(f"✅ Par aprovado: {pair_addr} → comprando {token}")
        amount_eth = Decimal(config["TRADE_SIZE_ETH"])
        amount_wei = int(amount_eth * 10**18)

        tx_hash = await buy(
            amount_in_wei=amount_wei,
            token_out=token,
            dex_router=dex_info.router,
            slippage_bps=config["SLIPPAGE_BPS"]
        )
        if not tx_hash:
            ERRORS.inc()
            send(f"❌ Falha na compra de {token} no par {pair_addr}")
            return

        BUY_SUCCESSES.inc()
        # registra posição
        price = DexClient(
            Web3=__import__("web3").Web3,  # evita conflito de imports
            router_address=dex_info.router
        ).get_token_price(token_address=token, weth_address=WETH) or 0.0
        add_position(pair=token, amount=amount_wei, avg_price=price)
        OPEN_POSITIONS.inc()

        send(
            "✅ Compra executada:\n"
            f"• Token: {token}\n"
            f"• Par: {pair_addr}\n"
            f"• TX: {tx_hash}\n"
            f"• Entrada: {price:.6f} WETH"
        )

    except Exception as e:
        ERRORS.inc()
        send(f"❌ Erro no pipeline para par {pair_addr}: {e}")
