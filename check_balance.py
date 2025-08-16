import os
from web3 import Web3

# Configurações — agora usando variável de ambiente para a chave
RPC_URL = "https://mainnet.base.org"
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # pega do ambiente no Render
if not PRIVATE_KEY:
    raise ValueError("⚠️ Variável de ambiente PRIVATE_KEY não encontrada!")

WETH_ADDRESS = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

# Inicializa Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))
acct = w3.eth.account.from_key(PRIVATE_KEY)
address = acct.address

# Função para obter saldo de token ERC20
def get_token_balance(token_address, wallet):
    abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
            "inputs": [{"name": "owner", "type": "address"}],
            "outputs": [{"type": "uint256"}]}]
    token = w3.eth.contract(address=token_address, abi=abi)
    balance = token.functions.balanceOf(wallet).call()
    return balance

# Saldos
eth_balance = w3.eth.get_balance(address) / 1e18
weth_balance = get_token_balance(WETH_ADDRESS, address) / 1e18

print(f"📍 Carteira: {address}")
print(f"💰 ETH:  {eth_balance:.6f}")
print(f"💰 WETH: {weth_balance:.6f}")
