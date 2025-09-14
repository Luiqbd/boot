# exit_manager.py

import asyncio
from config import config
from trading import sell

# armazenamento simples em memória; substitua por DB se quiser persistência
_positions: dict[str, dict] = {}
# cada valor: {"amount": int, "avg_price": float, "current_price": float}

async def check_exits():
    """
    Verifica todas as posições abertas e aplica:
      - Take Profit
      - Stop Loss
    Remove posição e executa venda quando condição atingir.
    """
    tp_pct = config["TAKE_PROFIT_PCT"]
    sl_pct = config["STOP_LOSS_PCT"]

    for pair, pos in list(_positions.items()):
        entry = pos["avg_price"]
        current = pos["current_price"]
        amount = pos["amount"]

        # check TP
        if current >= entry * (1 + tp_pct):
            tx = await sell(amount, pair)
            print(f"📈 Take Profit atingido em {pair}, tx={tx}")
            _positions.pop(pair, None)
            continue

        # check SL
        if current <= entry * (1 - sl_pct):
            tx = await sell(amount, pair)
            print(f"📉 Stop Loss atingido em {pair}, tx={tx}")
            _positions.pop(pair, None)
            continue
