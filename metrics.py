# metrics.py

from prometheus_client import Counter, Gauge, start_http_server

def init_metrics_server(port: int = 8000):
    start_http_server(port)

PAIRS_DISCOVERED = Counter(
    "sniper_pairs_discovered_total",
    "Total de pares novos detectados"
)
PAIRS_SKIPPED_NO_BASE = Counter(
    "sniper_pairs_skipped_no_base_total",
    "Total de pools pulados por não conter token base"
)
PAIRS_SKIPPED_LOW_LIQ = Counter(
    "sniper_pairs_skipped_low_liq_total",
    "Total de pools pulados por liquidez abaixo do mínimo"
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
OPEN_POSITIONS = Gauge(
    "sniper_open_positions",
    "Número de posições abertas"
)
