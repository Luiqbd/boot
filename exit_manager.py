import asyncio
from decimal import Decimal
from web3 import Web3

from config import config
from trading import sell
from storage import get_all_positions, remove_position
from dex_client import DexClient
from metrics import SELL_SUCCESSES, OPEN_POSITIONS
from notifier import send

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def check_exits() -> None:
    tp_pct = Decimal(str(config["TAKE_PROFIT_PCT"]))
    sl_pct = Decimal(str(config["STOP_LOSS_PCT"]))
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    primeiro_dex = config["DEXES"][0]

    for pair, amount, avg_price in get_all_positions():
        price = DexClient(web3, primeiro_dex.router) \
            .get_token_price(token_address=pair, weth_address=WETH)
        if price is None:
            continue

        price_dec = Decimal(str(price))
        entry_dec = Decimal(str(avg_price))

        if price_dec >= entry_dec * (1 + tp_pct):
            tx = await sell(amount, pair)
            SELL_SUCCESSES.inc()
            remove_position(pair)
            OPEN_POSITIONS.dec()
            send(
                "📈 TAKE PROFIT atingido:\n"
                f"• Token: {pair}\n"
                f"• TX: {tx}\n"
                f"• Lucro: +{tp_pct*100:.1f}%"
            )
            continue

        if price_dec <= entry_dec * (1 - sl_pct):
            tx = await sell(amount, pair)
            SELL_SUCCESSES.inc()
            remove_position(pair)
            OPEN_POSITIONS.dec()
            send(
                "📉 STOP LOSS atingido:\n"
                f"• Token: {pair}\n"
                f"• TX: {tx}\n"
                f"• Perda: –{sl_pct*100:.1f}%"
            )
            continue

    await asyncio.sleep(config["EXIT_POLL_INTERVAL"])
