import os
from web3 import Web3

RPC_URL = "https://mainnet.base.org"
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
if not PRIVATE_KEY:
    raise ValueError("⚠️ Variável de ambiente PRIVATE_KEY não encontrada!")

WETH_ADDRESS = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

w3 = Web3(Web3.HTTPProvider(RPC_URL))
acct = w3.eth.account.from_key(PRIVATE_KEY)
default_address = acct.address

# Função para obter saldo de token ERC20
def get_token_balance(token_address, wallet):
    abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
            "inputs": [{"name": "owner", "type": "address"}],
            "outputs": [{"type": "uint256"}]}]
    token = w3.eth.contract(address=token_address, abi=abi)
    balance = token.functions.balanceOf(wallet).call()
    return balance

# Função exportável para o bot
def get_wallet_status(wallet_address: str = None) -> str:
    wallet = wallet_address or default_address
    eth_balance = w3.eth.get_balance(wallet) / 1e18
    weth_balance = get_token_balance(WETH_ADDRESS, wallet) / 1e18

    return (
        f"📍 Carteira: {wallet}\n"
        f"💰 ETH:  {eth_balance:.6f}\n"
        f"💰 WETH: {weth_balance:.6f}"
    )

# Execução direta opcional
if __name__ == "__main__":
    print(get_wallet_status())
