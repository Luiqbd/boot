import os
import requests

def gerar_meu_token_externo(client_id: str, client_secret: str) -> str:
    """
    Chama o endpoint do Auth0 para obter um access_token via Client Credentials.
    """
    domain   = os.getenv("AUTH0_DOMAIN")
    audience = os.getenv("AUTH0_AUDIENCE")
    url      = f"https://{domain}/oauth/token"
    payload = {
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "audience":      audience
    }

    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Auth0 não retornou o access_token")
    return token
