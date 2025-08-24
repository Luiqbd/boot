import requests
import logging

log = logging.getLogger(__name__)

def is_contract_verified(token_address: str, api_key: str) -> bool:
    """Verifica se o contrato do token está verificado no BaseScan."""
    try:
        url = "https://api.basescan.org/api"
        params = {
            "module": "contract",
            "action": "getsourcecode",
            "address": token_address,
            "apikey": api_key
        }
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        # status "1" significa sucesso, e SourceCode não vazio significa contrato verificado
        if data.get("status") == "1" and data["result"] and data["result"][0].get("SourceCode"):
            return True
        return False
    except Exception as e:
        log.error(f"Erro ao checar verificação do contrato: {e}", exc_info=True)
        return False

def is_token_concentrated(token_address: str, api_key: str, top_limit_pct: float) -> bool:
    """
    Retorna True se algum holder tiver >= top_limit_pct% do supply.
    """
    try:
        url = "https://api.basescan.org/api"
        params = {
            "module": "token",
            "action": "tokenholderlist",
            "contractaddress": token_address,
            "apikey": api_key
        }
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
        return True  # Em caso de erro, assume risco
