# classifier.py

import asyncio
from config import config
from exchange_client import ExchangeClient

_client = ExchangeClient(config["DEXES"][0]["router"])

async def is_honeypot(token: str) -> bool:
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client._calcular_amount_out_min(
                amount_in=1_000,
                path=[token, config["WETH"]],
                slippage_bps=0
            )
        )
        return False
    except Exception:
        return True

async def should_buy(pair: str, t0: str, t1: str, dex_info: dict) -> bool:
    token = t1 if t0.lower() == config["WETH"].lower() else t0
    if await is_honeypot(token):
        print(f"🚫 Token honeypot: {token}")
        return False
    return True
