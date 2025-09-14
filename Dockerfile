# Escolhe imagem Python leve
FROM python:3.11-slim

# Diretório de trabalho
WORKDIR /app

# Evita prompts e mensagens desnecessárias
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    POETRY_VIRTUALENVS_CREATE=false

# Copia código e instala dependências
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt

# Copia todo o projeto
COPY . .

# Expõe a porta da API Flask (opcional)
EXPOSE 10000

# Comando padrão: executa em modo worker
CMD ["python", "main.py", "--worker"]
