#!/bin/bash

echo "Iniciando bot.py..."
python bot.py &

sleep 2  # Dá um tempinho pro Flask iniciar

echo "Iniciando telegram_bot.py..."
python telegram_bot.py
