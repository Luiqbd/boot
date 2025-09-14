# trading.py

import asyncio
from exchange_client import ExchangeClient
from config import config

# instância única de ExchangeClient usando o primeiro router configurado
_client = ExchangeClient(config["DEXES"][0]["router"])

async def buy(
    amount_in_wei: int,
    token_out: str,
    slippage_bps: int = None
) -> str:
    """
    Compra `token_out` usando `amount_in_wei` de ETH/WETH.
    Retorna tx_hash (real ou fake, no DRY_RUN).
    """
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

async def sell(
    amount_in: int,
    token_in: str,
    slippage_bps: int = None
) -> str:
    """
    Vende `amount_in` de `token_in` para ETH/WETH.
    Retorna tx_hash (real ou fake, no DRY_RUN).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _client.sell_token(
            token_in=token_in,
            token_out_weth=config["WETH"],
            amount_in=amount_in,
            slippage_bps=slippage_bps
        )
    )
