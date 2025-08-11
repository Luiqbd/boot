from dotenv import load_dotenv
load_dotenv()

from config import config
from web3 import Web3

web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
address = web3.eth.account.from_key(config["PRIVATE_KEY"]).address
balance = web3.eth.get_balance(address)

print(f"Endereço conectado: {address}")
print(f"Saldo: {web3.fromWei(balance, 'ether')} ETH")
