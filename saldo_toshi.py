from web3 import Web3
from eth_account import Account
import os
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()
RPC_URL = os.getenv("RPC_URL") or "https://mainnet.base.org"
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Conecta à rede Base
web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not web3.isConnected():
    raise Exception("❌ Não foi possível conectar à rede Base")

# Gera endereço da carteira
account = Account.from_key(PRIVATE_KEY)
WALLET_ADDRESS = Web3.toChecksumAddress(account.address)

# Contrato TOSHI
TOSHI_CONTRACT = "0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4"
DECIMALS = 18

# ABI mínima ERC-20
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]

# Consulta saldo
contract = web3.eth.contract(address=Web3.toChecksumAddress(TOSHI_CONTRACT), abi=ERC20_ABI)
raw_balance = contract.functions.balanceOf(WALLET_ADDRESS).call()
formatted_balance = raw_balance / (10 ** DECIMALS)

# Exibe resultado
print(f"💼 Carteira: {WALLET_ADDRESS}")
print(f"🔸 TOSHI: {formatted_balance:.4f}")
