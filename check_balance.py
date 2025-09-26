# check_balance.py

import logging
import time
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

from config import config

logger = logging.getLogger("balance")

if WEB3_AVAILABLE:
    w3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    DEFAULT_WALLET = config["WALLET"]
    WETH_ADDR = config["WETH"]
else:
    w3 = None
    DEFAULT_WALLET = None
    WETH_ADDR = None
    logger.warning("Web3 não disponível - funcionalidades de balance limitadas")

def get_token_balance(token_address: str, wallet: str, retries: int = 3, delay: float = 0.5) -> float:
    if not WEB3_AVAILABLE or not w3:
        logger.warning("Web3 não disponível - retornando balance 0")
        return 0.0
        
    abi = [{
        "name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs": [{"name":"owner","type":"address"}],
        "outputs":[{"type":"uint256"}]
    }]
    token = w3.eth.contract(address=token_address, abi=abi)
    for i in range(retries):
        try:
            raw = token.functions.balanceOf(wallet).call()
            return raw / 1e18
        except Exception as e:
            logger.error(f"[{i+1}/{retries}] Erro balanceOf: {e}")
            time.sleep(delay)
    return 0.0

def get_wallet_status(wallet_address: str = None) -> str:
    if not WEB3_AVAILABLE or not w3:
        return "❌ Web3 não disponível - não é possível verificar balance"
        
    wallet = wallet_address or DEFAULT_WALLET
    if not wallet:
        return "❌ Endereço da carteira não configurado"
        
    try:
        eth = w3.eth.get_balance(wallet) / 1e18
    except Exception as e:
        logger.error(f"Erro ETH balance: {e}")
        eth = 0.0
    weth = get_token_balance(WETH_ADDR, wallet)
    return (
        f"📍 Carteira: {wallet}\n"
        f"💰 ETH:  {eth:.6f}\n"
        f"💰 WETH: {weth:.6f}"
    )
