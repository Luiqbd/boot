#!/usr/bin/env bash
set -euo pipefail

# Se estiver em dev e quiser .env local:
# [ -f .env ] && source .env

# Captura SIGINT/SIGTERM e repassa pra Python
trap 'echo "🛑 Sinal recebido, fechando..."; kill "$pid"; wait "$pid"' SIGINT SIGTERM

echo "🚀 Iniciando Sniper Bot..."
# -u = unbuffered stdout/stderr; exec = substitui shell pelo Python
exec python -u main.py &
pid=$!

# Espera o processo Python terminar (ou receber sinal)
wait "$pid"
