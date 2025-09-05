# token_service.py
import os
import requests
import logging

logger = logging.getLogger(__name__)

def gerar_meu_token_externo() -> str:
    # só aqui a gente lê e valida as env-vars
    domain        = os.getenv("AUTH0_DOMAIN")
    audience      = os.getenv("AUTH0_AUDIENCE")
    client_id     = os.getenv("AUTH0_CLIENT_ID")
    client_secret = os.getenv("AUTH0_CLIENT_SECRET")

    if not domain:
        raise RuntimeError("AUTH0_DOMAIN não definido")
    if not audience:
        raise RuntimeError("AUTH0_AUDIENCE não definido")
    if not client_id or not client_secret:
        raise RuntimeError("AUTH0_CLIENT_ID e AUTH0_CLIENT_SECRET devem ser definidos")

    url = f"https://{domain}/oauth/token"
    payload = {
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "audience":      audience
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Falha ao obter token do Auth0: %s", e)
        raise RuntimeError(f"Falha ao obter token do Auth0: {e}") from e

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Auth0 não retornou access_token, resposta: {data}")

    return token
