# pipeline.py

import asyncio
from decimal import Decimal
from web3 import Web3

from config import config
from classifier import should_buy
from trading import buy
from exit_manager import _positions
from dex_client import DexClient

async def on_pair(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: dict
):
    """
    Chamado a cada par novo:
      1) classifica com should_buy
      2) compra via buy()
      3) armazena posição para exit_manager
    """
    # 1) decide se vale a pena
    if not await should_buy(pair_addr, token0, token1, dex_info):
        return

    # 2) define tamanho do trade e converte para wei
    tamanho_eth = Decimal(str(config["TRADE_SIZE_ETH"]))
    amount_wei = int(tamanho_eth * Decimal(10**18))

    # 3) identifica qual token não é WETH
    weth = config["WETH"].lower()
    target = token1 if token0.lower() == weth else token0

    # 4) executa a compra
    tx_hash = await buy(amount_in_wei=amount_wei, token_out=target)
    if not tx_hash:
        print(f"🚫 Falha ao comprar {target} no par {pair_addr}")
        return

    # 5) obtem preço de entrada on-chain
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    dex = DexClient(web3, dex_info["router"])
    preco = dex.get_token_price(token_address=target, weth_address=config["WETH"])
    preco = preco or 0.0

    # 6) registra posição para exit_manager
    _positions[pair_addr] = {
        "amount": amount_wei,
        "avg_price": preco,
        "current_price": preco
    }
    print(f"✅ Comprado {target} no par {pair_addr}  TX={tx_hash}  PreçoEntrada={preco:.6f} WETH")
