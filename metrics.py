try:
    from prometheus_client import Counter, Gauge, start_http_server
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    
    # Mock classes para quando prometheus não estiver disponível
    class MockCounter:
        def __init__(self, *args, **kwargs):
            pass
        def inc(self, amount=1):
            pass
        def labels(self, **kwargs):
            return self
    
    class MockGauge:
        def __init__(self, *args, **kwargs):
            pass
        def set(self, value):
            pass
        def inc(self, amount=1):
            pass
        def dec(self, amount=1):
            pass
        def labels(self, **kwargs):
            return self
    
    Counter = MockCounter
    Gauge = MockGauge

def init_metrics_server(port: int = 8000):
    if PROMETHEUS_AVAILABLE:
        start_http_server(port)
    else:
        print(f"Prometheus não disponível - servidor de métricas não iniciado na porta {port}")

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
