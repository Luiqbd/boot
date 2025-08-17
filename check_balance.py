from config import config
from web3 import Web3
import logging, os, time

log = logging.getLogger("balance")

w3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
default_address = Web3.to_checksum_address(
    w3.eth.account.from_key(config["PRIVATE_KEY"]).address
)
WETH_ADDRESS = Web3.to_checksum_address(config["WETH"])

def get_token_balance(token_address, wallet, retries=3, delay=0.5):
    abi = [{
        "name": "balanceOf", "type": "function", "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"type": "uint256"}]
    }]
    token_address = Web3.to_checksum_address(token_address)
    wallet = Web3.to_checksum_address(wallet)
    token = w3.eth.contract(address=token_address, abi=abi)

    for attempt in range(1, retries+1):
        try:
            return token.functions.balanceOf(wallet).call()
        except Exception as e:
            log.error(f"[{attempt}/{retries}] Erro ao consultar saldo de token {token_address} para {wallet}: {e}")
            time.sleep(delay)
    return 0

def get_wallet_status(wallet_address=None):
    wallet = Web3.to_checksum_address(wallet_address or default_address)
    try:
        eth_balance = w3.eth.get_balance(wallet) / 1e18
    except Exception as e:
        log.error(f"Erro ao consultar ETH de {wallet}: {e}")
        eth_balance = 0
    weth_balance = get_token_balance(WETH_ADDRESS, wallet) / 1e18

    return (
        f"📍 Carteira: {wallet}\n"
        f"💰 ETH:  {eth_balance:.6f}\n"
        f"💰 WETH: {weth_balance:.6f}"
    )

if __name__ == "__main__":
    print(get_wallet_status())
