# metrics.py

from prometheus_client import Counter, Gauge, start_http_server

def init_metrics_server(port: int = 8000):
    """
    Inicia servidor HTTP para Prometheus no porto especificado.
    """
    start_http_server(port)

# Contadores de eventos
PAIRS_DISCOVERED = Counter(
    "sniper_pairs_discovered_total",
    "Total de pares novos detectados"
)
BUY_ATTEMPTS = Counter(
    "sniper_buy_attempts_total",
    "Total de tentativas de compra efetuadas"
)
BUY_SUCCESSES = Counter(
    "sniper_buy_success_total",
    "Total de compras bem-sucedidas"
)
SELL_SUCCESSES = Counter(
    "sniper_sell_success_total",
    "Total de vendas bem-sucedidas"
)
ERRORS = Counter(
    "sniper_errors_total",
    "Total de erros não tratados no pipeline"
)

# Gauge para posições abertas
OPEN_POSITIONS = Gauge(
    "sniper_open_positions",
    "Número de posições abertas no momento"
)
