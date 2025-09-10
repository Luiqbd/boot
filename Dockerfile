# Dockerfile

# 1) Base enxuta e com dependências de build
FROM python:3.10-slim AS builder

# 2) Variáveis de ambiente para pip e flask
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_ENV=production

# 3) Instala as libs de sistema que o pip possa precisar
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      libssl-dev \
      libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 4) Copia só o requirements e instala
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# 5) Copia o restante da aplicação
FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_ENV=production

WORKDIR /app

# 6) Copia as deps instaladas do stage anterior
COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /app /app

# 7) Cria user não-root
RUN groupadd -r appuser && useradd --no-log-init -r -g appuser appuser
USER appuser

# 8) Expondo porta do Flask
EXPOSE 10000

# 9) Healthcheck usando endpoint simples
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:10000/healthz || exit 1

# 10) Entry point minimalista
ENTRYPOINT ["python", "main.py"]
