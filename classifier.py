# classifier.py

import asyncio
from config import config
from exchange_client import ExchangeClient

# Pega o primeiro DexConfig e usa .router
primeiro_dex = config["DEXES"][0]
_client = ExchangeClient(primeiro_dex.router)

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

async def should_buy(
    pair_addr: str,
    token0: str,
    token1: str,
    dex_info: Any
) -> bool:
    token = token1 if token0.lower() == config["WETH"].lower() else token0
    if await is_honeypot(token):
        return False
    return True
