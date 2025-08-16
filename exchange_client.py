from web3 import Web3
from dotenv import load_dotenv
import os
import json
import logging

load_dotenv()
log = logging.getLogger("exchange")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

class ExchangeClient:
    def __init__(self):
        self.web3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
        self.wallet = os.getenv("WALLET_ADDRESS")
        self.private_key = os.getenv("PRIVATE_KEY")

        if not self.wallet or not self.private_key:
            raise ValueError("❌ WALLET_ADDRESS ou PRIVATE_KEY não definidos no ambiente.")

        self.router_address = Web3.to_checksum_address("0xE592427A0AEce92De3Edee1F18E0157C05861564")  # Uniswap V3 Router

        try:
            with open("abi/uniswap_v3_router_abi.json") as f:
                self.router_abi = json.load(f)
            with open("abi/erc20.json") as f:
                self.erc20_abi = json.load(f)
        except Exception as e:
            raise FileNotFoundError(f"❌ Erro ao carregar arquivos ABI: {e}")

        self.router = self.web3.eth.contract(address=self.router_address, abi=self.router_abi)

    def approve_token(self, token_address, amount_wei):
        try:
            token = self.web3.eth.contract(address=token_address, abi=self.erc20_abi)
            tx = token.functions.approve(self.router_address, amount_wei).build_transaction({
                "from": self.wallet,
                "gas": 100000,
                "gasPrice": self.web3.to_wei("5", "gwei"),
                "nonce": self.web3.eth.get_transaction_count(self.wallet),
            })
            signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            log.info(f"✅ Token aprovado: {token_address} | TX: {self.web3.to_hex(tx_hash)}")
            return self.web3.to_hex(tx_hash)
        except Exception as e:
            log.error(f"❌ Erro ao aprovar token: {e}")
            raise

    def buy_token(self, token_in, token_out, amount_in_wei):
        try:
            deadline = self.web3.eth.get_block("latest")["timestamp"] + 300
            tx = self.router.functions.exactInputSingle({
                "tokenIn": token_in,
                "tokenOut": token_out,
                "fee": 3000,
                "recipient": self.wallet,
                "deadline": deadline,
                "amountIn": amount_in_wei,
                "amountOutMinimum": 0,
                "sqrtPriceLimitX96": 0
            }).build_transaction({
                "from": self.wallet,
                "value": amount_in_wei,
                "gas": 300000,
                "gasPrice": self.web3.to_wei("5", "gwei"),
                "nonce": self.web3.eth.get_transaction_count(self.wallet),
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            return self.web3.to_hex(tx_hash)
        except Exception as e:
            log.error(f"❌ Erro na compra: {e}")
            raise

    def sell_token(self, token_in, token_out, amount_in_wei):
        try:
            self.approve_token(token_in, amount_in_wei)
            deadline = self.web3.eth.get_block("latest")["timestamp"] + 300

            tx = self.router.functions.exactInputSingle({
                "tokenIn": token_in,
                "tokenOut": token_out,
                "fee": 3000,
                "recipient": self.wallet,
                "deadline": deadline,
                "amountIn": amount_in_wei,
                "amountOutMinimum": 0,
                "sqrtPriceLimitX96": 0
            }).build_transaction({
                "from": self.wallet,
                "gas": 300000,
                "gasPrice": self.web3.to_wei("5", "gwei"),
                "nonce": self.web3.eth.get_transaction_count(self.wallet),
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            return self.web3.to_hex(tx_hash)
        except Exception as e:
            log.error(f"❌ Erro na venda: {e}")
            raise
