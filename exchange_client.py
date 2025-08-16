from web3 import Web3
from dotenv import load_dotenv
import os
import json

load_dotenv()

class ExchangeClient:
    def __init__(self):
        self.web3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
        self.wallet = os.getenv("WALLET_ADDRESS")
        self.private_key = os.getenv("PRIVATE_KEY")
        self.router_address = Web3.toChecksumAddress("0xE592427A0AEce92De3Edee1F18E0157C05861564")  # Uniswap V3 Router
        with open("uniswap_v3_router_abi.json") as f:
            self.router_abi = json.load(f)
        self.router = self.web3.eth.contract(address=self.router_address, abi=self.router_abi)

    def buy_token(self, token_in, token_out, amount_in_wei):
        deadline = self.web3.eth.getBlock("latest")["timestamp"] + 300

        tx = self.router.functions.exactInputSingle({
            "tokenIn": token_in,
            "tokenOut": token_out,
            "fee": 3000,
            "recipient": self.wallet,
            "deadline": deadline,
            "amountIn": amount_in_wei,
            "amountOutMinimum": 0,
            "sqrtPriceLimitX96": 0
        }).buildTransaction({
            "from": self.wallet,
            "value": amount_in_wei,
            "gas": 300000,
            "gasPrice": self.web3.toWei("5", "gwei"),
            "nonce": self.web3.eth.getTransactionCount(self.wallet),
        })

        signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.web3.eth.sendRawTransaction(signed_tx.rawTransaction)
        return self.web3.toHex(tx_hash)
