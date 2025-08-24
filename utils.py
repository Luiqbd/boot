import requests
import logging

log = logging.getLogger(__name__)

# Endpoints
ETHERSCAN_V1_URL = "https://api.basescan.org/api"
ETHERSCAN_V2_URL = "https://api.etherscan.io/api"
CHAIN_ID = "base-mainnet"  # usado apenas na V2

def is_v2_key(api_key: str) -> bool:
    """Detecta se a chave é da API V2 (Multichain) por padrão de prefixo ou tamanho."""
    return api_key.startswith("CX") or len(api_key) > 40

def is_contract_verified(token_address: str, api_key: str) -> bool:
    """Verifica se o contrato está verificado, usando V1 ou V2 conforme a chave."""
    try:
        if is_v2_key(api_key):
            # V2 Multichain
            params = {
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
                "chain": CHAIN_ID,
                "apikey": api_key
            }
            url = ETHERSCAN_V2_URL
        else:
            # V1 BaseScan
            params = {
                "module": "contract",
                "action": "getsourcecode",
                "address": token_address,
                "apikey": api_key
            }
            url = ETHERSCAN_V1_URL

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "1" and data["result"] and data["result"][0].get("SourceCode"):
            return True
        return False
    except Exception as e:
        log.error(f"Erro ao verificar contrato: {e}", exc_info=True)
        return False

def is_token_concentrated(token_address: str, api_key: str, top_limit_pct: float) -> bool:
    """Verifica concentração de holders, usando V1 ou V2 conforme a chave."""
    try:
        if is_v2_key(api_key):
            # V2 Multichain
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "chain": CHAIN_ID,
                "apikey": api_key
            }
            url = ETHERSCAN_V2_URL
        else:
            # V1 BaseScan
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "apikey": api_key
            }
            url = ETHERSCAN_V1_URL

        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        for holder in data.get("result", []):
            pct_str = holder.get("Percentage", "0").replace("%", "").strip()
            try:
                pct = float(pct_str)
            except ValueError:
                pct = 0.0
            if pct >= top_limit_pct:
                return True
        return False
    except Exception as e:
        log.error(f"Erro ao verificar concentração de holders: {e}", exc_info=True)
        return True
