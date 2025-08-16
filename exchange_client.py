import os
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

class ExchangeClient:
    def __init__(self):
        self.web3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
        self.wallet = os.getenv("WALLET_ADDRESS")
        self.private_key = os.getenv("PRIVATE_KEY")

    def buy_token(self, token_address, amount_eth):
        # Aqui você pode usar Uniswap Router para swap ETH → token
        # Exemplo simplificado: enviar ETH direto (não recomendado para tokens ERC-20)
        tx = {
            "to": token_address,
            "value": self.web3.toWei(amount_eth, "ether"),
            "gas": 200000,
            "gasPrice": self.web3.toWei("5", "gwei"),
            "nonce": self.web3.eth.getTransactionCount(self.wallet),
        }
        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.web3.eth.sendRawTransaction(signed_tx.rawTransaction)
        return self.web3.toHex(tx_hash)

    def sell_token(self, token_address):
        # Aqui você implementa swap token → ETH via Uniswap
        pass
