# exit_manager.py

import asyncio
import time
from decimal import Decimal
from web3 import Web3

from config import config
from trading import sell
from storage import get_all_positions, remove_position
from dex_client import DexClient

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def check_exits() -> None:
    """
    Percorre todas as posições salvas e, para cada uma:
      - consulta preço atual on-chain
      - aplica Take Profit e Stop Loss
      - executa sell() e remove da storage
    """
    tp_pct = Decimal(str(config["TAKE_PROFIT_PCT"]))
    sl_pct = Decimal(str(config["STOP_LOSS_PCT"]))

    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    for pair, amount, avg_price in get_all_positions():
        dex = DexClient(web3, config["DEXES"][0]["router"])
        price = dex.get_token_price(token_address=pair, weth_address=WETH)
        if price is None:
            continue

        price_dec = Decimal(str(price))
        entry_dec = Decimal(str(avg_price))

        # Take Profit
        if price_dec >= entry_dec * (1 + tp_pct):
            tx = await sell(amount, pair)
            print(f"📈 TP atingido em {pair}, tx={tx}")
            remove_position(pair)
            continue

        # Stop Loss
        if price_dec <= entry_dec * (1 - sl_pct):
            tx = await sell(amount, pair)
            print(f"📉 SL atingido em {pair}, tx={tx}")
            remove_position(pair)
            continue

    # aguarda próximo ciclo
    await asyncio.sleep(config["EXIT_POLL_INTERVAL"])
