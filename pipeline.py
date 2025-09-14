# pipeline.py

import asyncio
from decimal import Decimal
from web3 import Web3

from config import config
from classifier import should_buy
from trading import buy
from storage import add_position
from dex_client import DexClient

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def on_pair(pair: str, token0: str, token1: str, dex_info: dict) -> None:
    """
    Chamado a cada par novo:
      1) should_buy → True/False
      2) buy() se aprovado
      3) obtem preço e add_position
    """
    # 1) classificação
    ok = await should_buy(pair, token0, token1, dex_info)
    if not ok:
        return

    # 2) define quantidade e executa compra
    trade_size = Decimal(str(config["TRADE_SIZE_ETH"]))
    amount_wei = int(trade_size * Decimal(10**18))

    # escolhe token alvo
    target = token1 if token0.lower() == WETH.lower() else token0

    tx_hash = await buy(amount_in_wei=amount_wei, token_out=target)
    if not tx_hash:
        print(f"🚫 Falha na compra de {target} no par {pair}")
        return

    # 3) obtém preço de entrada on-chain
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    dex = DexClient(web3, dex_info["router"])
    price = dex.get_token_price(token_address=target, weth_address=WETH) or 0.0

    add_position(pair=target, amount=amount_wei, avg_price=price)
    print(f"✅ Comprado {target} | Par={pair} | TX={tx_hash} | PreçoEntrada={price:.6f}")
