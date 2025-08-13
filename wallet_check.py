from web3 import Web3
from dotenv import load_dotenv
import os

# Carrega variáveis do .env
load_dotenv()
RPC_URL = os.getenv("RPC_URL")

# Conecta à rede Base
web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not web3.isConnected():
    raise Exception("Não foi possível conectar à rede Base")

# Define o contrato do TOSHI na Base
TOKENS = {
    "TOSHI": {
        "address": "0x11FFd70009F195cFb1fb908dae04B9AD6b5630dD",
        "decimals": 18
    }
}

# ABI mínima para saldo
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }
]

# Endereço da sua carteira
wallet_address = "0x3a94c149332d54481e9e956c4f38862b5329e52b947e7942a32463db1e192c56"

# Instancia o contrato
token = TOKENS["TOSHI"]
contract = web3.eth.contract(address=Web3.toChecksumAddress(token["address"]), abi=ERC20_ABI)

# Consulta o saldo
raw_balance = contract.functions.balanceOf(wallet_address).call()
formatted_balance = raw_balance / (10 ** token["decimals"])

print(f"Saldo de TOSHI na carteira {wallet_address}: {formatted_balance:.4f} TOSHI")
