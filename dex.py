import json
from web3 import Web3
from web3.middleware import geth_poa_middleware
from config import config

class DexClient:
    def __init__(self, rpc_url, private_key):
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.account = self.web3.eth.account.from_key(private_key)
        self.address = self.account.address

        # Carrega contrato do router
        with open("abis/uniswap_router.json") as f:
            abi = json.load(f)
        self.router = self.web3.eth.contract(address=config["DEX_ROUTER"], abi=abi)

    def get_price(self):
        amount_in = self.web3.to_wei(1, 'ether')
        path = [config["WETH"], config["USDC"]]
        try:
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
            return self.web3.from_wei(amounts[-1], 'ether')
        except Exception as e:
            print(f"❌ Erro ao consultar preço: {e}")
            return None

    def buy(self):
        try:
            tx = self.router.functions.swapExactETHForTokens(
                0,  # slippage mínima
                [config["WETH"], config["USDC"]],
                self.address,
                int(self.web3.eth.get_block('latest')['timestamp']) + config["TX_DEADLINE_SEC"]
            ).build_transaction({
                'from': self.address,
                'value': self.web3.to_wei(0.001, 'ether'),
                'gas': 250000,
                'gasPrice': self.web3.to_wei('5', 'gwei'),
                'nonce': self.web3.eth.get_transaction_count(self.address),
                'chainId': config["CHAIN_ID"]
            })

            signed = self.account.sign_transaction(tx)
            tx_hash = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            print(f"✅ Compra enviada: {self.web3.to_hex(tx_hash)}")
        except Exception as e:
            print(f"❌ Erro ao comprar: {e}")

    def sell(self):
        print("⚠️ Venda ainda não implementada — requer aprovação do token.")
