#!/bin/bash

# 🧹 Mata qualquer instância anterior de main.py (se houver)
pkill -f main.py || true

echo "Iniciando bot unificado..."
python main.py

