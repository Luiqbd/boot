# exit_manager.py

import asyncio
from decimal import Decimal
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

from config import config
from trading import sell
from storage import get_all_positions, remove_position
from dex_client import DexClient
from metrics import SELL_SUCCESSES, OPEN_POSITIONS
from notifier import send

RPC_URL = config["RPC_URL"]
WETH    = config["WETH"]

async def check_exits() -> None:
    tp = Decimal(str(config["TAKE_PROFIT_PCT"]))
    sl = Decimal(str(config["STOP_LOSS_PCT"]))
    web3 = Web3(Web3.HTTPProvider(RPC_URL))
    dex_router = config["DEXES"][0].router

    for pair, amount, avg_price in get_all_positions():
        price = DexClient(web3, dex_router) \
            .get_token_price(token_address=pair, weth_address=WETH)
        if price is None:
            continue

        price_dec = Decimal(str(price))
        entry_dec = Decimal(str(avg_price))

        # Take Profit
        if price_dec >= entry_dec * (1 + tp):
            tx = await sell(
                amount=amount,
                token_in=pair,
                dex_router=dex_router,
                slippage_bps=config["SLIPPAGE_BPS"]
            )
            SELL_SUCCESSES.inc()
            remove_position(pair)
            OPEN_POSITIONS.dec()
            send(
                "📈 TAKE PROFIT atingido:\n"
                f"• Token: {pair}\n"
                f"• TX: {tx}\n"
                f"• Lucro: +{tp*100:.1f}%"
            )
            continue

        # Stop Loss
        if price_dec <= entry_dec * (1 - sl):
            tx = await sell(
                amount=amount,
                token_in=pair,
                dex_router=dex_router,
                slippage_bps=config["SLIPPAGE_BPS"]
            )
            SELL_SUCCESSES.inc()
            remove_position(pair)
            OPEN_POSITIONS.dec()
            send(
                "📉 STOP LOSS atingido:\n"
                f"• Token: {pair}\n"
                f"• TX: {tx}\n"
                f"• Perda: –{sl*100:.1f}%"
            )
            continue

    await asyncio.sleep(config["EXIT_POLL_INTERVAL"])
