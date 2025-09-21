# check_balance.py

import logging
import time
from web3 import Web3

from config import config

logger = logging.getLogger("balance")

w3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
DEFAULT_WALLET = config["WALLET"]
WETH_ADDR = config["WETH"]

def get_token_balance(token_address: str, wallet: str, retries: int = 3, delay: float = 0.5) -> float:
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
    wallet = wallet_address or DEFAULT_WALLET
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
