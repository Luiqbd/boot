from prometheus_client import Counter, Gauge, start_http_server

# Métricas principais
sniper_pairs_detected = Counter("sniper_pairs_detected_total", "Total de pares detectados")
sniper_liquidity_skipped = Counter("sniper_liquidity_skipped_total", "Pares ignorados por liquidez insuficiente")
sniper_runtime = Gauge("sniper_runtime_seconds", "Tempo de execução do sniper em segundos")
sniper_active = Gauge("sniper_active", "Estado atual do sniper (1=ativo, 0=inativo)")

def start_metrics_server(port=8000):
    start_http_server(port)
