# pipeline.py

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

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def on_pair(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: Any
) -> None:
    """
    Pipeline completo para cada par novo:
      1) Métrica BUY_ATTEMPTS
      2) Filtro should_buy()
      3) Compra via buy()
      4) Métrica BUY_SUCCESSES
      5) Persiste posição com add_position()
    """
    try:
        BUY_ATTEMPTS.inc()

        # Filtro
        aprovado = await should_buy(pair_addr, token0, token1, dex_info)
        if not aprovado:
            return

        # Calcula quantidade em wei
        trade_size = Decimal(str(config["TRADE_SIZE_ETH"]))
        amount_wei = int(trade_size * Decimal(10**18))

        # Define token alvo
        target = token1 if token0.lower() == WETH.lower() else token0

        # Executa compra
        tx_hash = await buy(amount_in_wei=amount_wei, token_out=target)
        if not tx_hash:
            return

        BUY_SUCCESSES.inc()

        # Captura preço on-chain
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        dex = DexClient(web3, dex_info.router)
        price = dex.get_token_price(token_address=target, weth_address=WETH) or 0.0

        # Persiste posição
        add_position(pair=target, amount=amount_wei, avg_price=price)
        print(f"✅ Comprado {target} | Par={pair_addr} | TX={tx_hash} | Entrada={price:.6f}")
    except Exception:
        ERRORS.inc()
        raise
