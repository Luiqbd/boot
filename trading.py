# trading.py

import asyncio
from typing import Any, Optional

from config import config
from exchange_client import ExchangeClient

async def buy(
    amount_in_wei: int,
    token_out: str,
    dex_router: str,
    slippage_bps: Optional[int] = None
) -> Optional[str]:
    loop = asyncio.get_event_loop()
    client = ExchangeClient(dex_router)
    try:
        return await loop.run_in_executor(
            None,
            lambda: client.buy_token(
                token_in_weth=config["WETH"],
                token_out=token_out,
                amount_in_wei=amount_in_wei,
                slippage_bps=slippage_bps
            )
        )
    except Exception:
        return None

async def sell(
    amount: int,
    token_in: str,
    dex_router: str,
    slippage_bps: Optional[int] = None
) -> Optional[str]:
    loop = asyncio.get_event_loop()
    client = ExchangeClient(dex_router)
    try:
        return await loop.run_in_executor(
            None,
            lambda: client.sell_token(
                token_in=token_in,
                token_out_weth=config["WETH"],
                amount_in=amount,
                slippage_bps=slippage_bps
            )
        )
    except Exception:
        return None
