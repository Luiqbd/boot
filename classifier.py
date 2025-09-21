# classifier.py

import asyncio
from typing import Any

from config import config
from exchange_client import ExchangeClient

async def is_honeypot(token: str, router: str) -> bool:
    """
    Simula swap token→WETH para detectar honeypot.
    """
    client = ExchangeClient(router)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: client._calc_amounts(
                amount_in=10**6,  # pequena amostra
                path=[token, config["WETH"]],
                slippage_bps=0
            )
        )
        return False
    except Exception:
        return True

async def should_buy(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: Any
) -> bool:
    token = token1 if token0.lower() == config["WETH"].lower() else token0
    if await is_honeypot(token, dex_info.router):
        return False
    return True
