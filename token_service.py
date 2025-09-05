import os
import requests

# Carrega configurações do Auth0
AUTH0_DOMAIN        = os.getenv("AUTH0_DOMAIN")
AUTH0_AUDIENCE      = os.getenv("AUTH0_AUDIENCE")
AUTH0_CLIENT_ID     = os.getenv("AUTH0_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET")

# Validação antecipada de variáveis de ambiente
if not AUTH0_DOMAIN:
    raise RuntimeError("AUTH0_DOMAIN não definido")
if not AUTH0_AUDIENCE:
    raise RuntimeError("AUTH0_AUDIENCE não definido")
if not AUTH0_CLIENT_ID or not AUTH0_CLIENT_SECRET:
    raise RuntimeError("AUTH0_CLIENT_ID e AUTH0_CLIENT_SECRET devem ser definidos")

def gerar_meu_token_externo() -> str:
    """
    Obtém um access_token do Auth0 via Client Credentials.
    """
    url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type":    "client_credentials",
        "client_id":     AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "audience":      AUTH0_AUDIENCE
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Falha ao obter token do Auth0: {e}") from e

    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Auth0 não retornou access_token, resposta: {data}")

    return token
