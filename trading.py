# trading.py

import asyncio
from exchange_client import ExchangeClient
from config import config

_client = ExchangeClient(config["DEXES"][0]["router"])

async def buy(amount_in_wei: int, token_out: str, slippage_bps: int = None) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _client.buy_token(
            token_in_weth=config["WETH"],
            token_out=token_out,
            amount_in_wei=amount_in_wei,
            slippage_bps=slippage_bps
        )
    )

async def sell(amount: int, token_in: str, slippage_bps: int = None) -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _client.sell_token(
            token_in=token_in,
            token_out_weth=config["WETH"],
            amount_in=amount,
            slippage_bps=slippage_bps
        )
    )
