# Trading Bot

Bot de trading automatizado com estratégia simples e integração com DEX.

## Como usar

1. Clone o projeto
2. Crie um arquivo `.env` baseado no `.env.example`
3. Execute com Python ou via Docker

## Deploy com Docker

```bash
docker build -t trading-bot .
docker run --env-file .env trading-bot