# classifier.py

import asyncio
from config import config
from exchange_client import ExchangeClient

# instância única do client de trading (router da primeira DEX)
_client = ExchangeClient(config["DEXES"][0]["router"])

async def is_honeypot(token: str) -> bool:
    """
    Testa se um token é honeypot tentando estimar saída ɣ ETH→token→ETH.
    Se a simulação falhar, considera honeypot.
    """
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _client._calcular_amount_out_min(
                amount_in=1_000,  # 0.000001 token
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
    dex_info: dict
) -> bool:
    """
    Retorna True se todas as checagens iniciais forem aprovadas:
      - não é honeypot
      - (no futuro) outras heurísticas: liquidez extra, taxa, ML, sentiment
    """
    # define token alvo (aquele que não é WETH)
    token = token1 if token0.lower() == config["WETH"].lower() else token0

    # 1) honeypot
    if await is_honeypot(token):
        return False

    # 2) (outros checks podem entrar aqui)

    return True
