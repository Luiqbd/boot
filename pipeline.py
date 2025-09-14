# pipeline.py

import asyncio
from decimal import Decimal
from web3 import Web3

from config import config
from classifier import should_buy
from trading import buy
from storage import add_position
from dex_client import DexClient
from metrics import BUY_ATTEMPTS, BUY_SUCCESSES, ERRORS

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def on_pair(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: Any   # agora DexInfo, não dict
) -> None:
    try:
        BUY_ATTEMPTS.inc()

        ok = await should_buy(pair_addr, token0, token1, dex_info)
        if not ok:
            return

        tamanho_eth = Decimal(str(config["TRADE_SIZE_ETH"]))
        amount_wei = int(tamanho_eth * Decimal(10**18))

        target = token1 if token0.lower() == WETH.lower() else token0

        tx_hash = await buy(amount_in_wei=amount_wei, token_out=target)
        if not tx_hash:
            return

        BUY_SUCCESSES.inc()

        # captura preço on-chain
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        dex = DexClient(web3, dex_info.router)  # usa atributo .router
        price = dex.get_token_price(token_address=target, weth_address=WETH) or 0.0

        add_position(pair=target, amount=amount_wei, avg_price=price)
        print(f"✅ Comprado {target} | Par={pair_addr} | TX={tx_hash} | Entrada={price:.6f}")
    except Exception:
        ERRORS.inc()
        raise
