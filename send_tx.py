import os
from web3 import Web3
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

# Conecta à rede Base
rpc_url = os.getenv("RPC_URL")
web3 = Web3(Web3.HTTPProvider(rpc_url))

# Verifica conexão
if not web3.is_connected():
    print("❌ Não foi possível conectar à rede Base")
    exit()

# Chave privada e conta
private_key = os.getenv("PRIVATE_KEY")
account = web3.eth.account.from_key(private_key)
sender = account.address

print(f"🔗 Conectado como: {sender}")

# Destinatário (exemplo: endereço "burn")
recipient = "0x000000000000000000000000000000000000dead"

# Monta a transação
tx = {
    'to': recipient,
    'value': web3.to_wei(0.001, 'ether'),  # envia 0.001 ETH
    'gas': 21000,
    'gasPrice': web3.to_wei('5', 'gwei'),
    'nonce': web3.eth.get_transaction_count(sender),
    'chainId': 8453  # Chain ID da Base Mainnet
}

# Assina e envia
signed_tx = account.sign_transaction(tx)
tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

print(f"✅ Transação enviada: {web3.to_hex(tx_hash)}")
