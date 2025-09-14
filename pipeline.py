# pipeline.py

import asyncio
from classifier import should_buy
from trading import buy
from exit_manager import _positions
from config import config
from dex_client import DexClient

async def on_pair(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: dict
):
    """
    Função chamada a cada novo par descoberto.
    Executa:
      1) classificação (should_buy)
      2) compra (buy)
      3) registro em _positions para exit_manager
    """
    # 1) decide se compra
    if not await should_buy(pair_addr, token0, token1, dex_info):
        return

    # 2) define quantidade em ETH e converte para wei
    trade_size = config["TRADE_SIZE_ETH"]
    amount_wei = int(trade_size * 10**18)

    # 3) identifica token alvo (não WETH)
    target = token1 if token0.lower() == config["WETH"].lower() else token0

    # 4) realiza compra
    tx = await buy(amount_in_wei=amount_wei, token_out=target)
    if not tx:
        print(f"🚫 Falha ao comprar {target} no par {pair_addr}")
        return

    # 5) obtém preço de entrada on-chain e armazena posição
    dex = DexClient(Web3.HTTPProvider(config["RPC_URL"]), dex_info["router"])
    price = dex.get_token_price(target, config["WETH"]) or 0.0

    _positions[pair_addr] = {
        "amount": amount_wei,
        "avg_price": price,
        "current_price": price
    }
    print(f"✅ Comprado {target} no par {pair_addr} → tx={tx}, preço={price:.6f} WETH")
