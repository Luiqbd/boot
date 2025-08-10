#!/bin/bash

# Inicia o primeiro bot em segundo plano
echo "Iniciando bot.py..."
python bot.py &

# Inicia o segundo bot
echo "Iniciando telegram_bot.py..."
python telegram_bot.py
