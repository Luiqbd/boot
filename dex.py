import json
import random
from config import config

class DexClient:
    def __init__(self, web3):
        self.web3 = web3
        self.account = web3.eth.account.from_key(config["PRIVATE_KEY"])
        self.address = self.account.address

        # Carrega ABI do DEX router
        with open("abis/router.json") as f:
            router_abi = json.load(f)
        self.router = web3.eth.contract(address=config["DEX_ROUTER"], abi=router_abi)

    def get_token_price(self, token_address: str) -> float:
        # Simulação de preço para TOSHI
        # Em breve podemos trocar por Dexscreener ou outro agregador
        return round(random.uniform(0.0005, 0.0015), 6)

    def sell(self):
        try:
            with open("abis/erc20.json") as f:
                erc20_abi = json.load(f)
            usdc = self.web3.eth.contract(address=config["USDC"], abi=erc20_abi)

            amount_in = usdc.functions.allowance(self.address, config["DEX_ROUTER"]).call()
            if amount_in == 0:
                balance = usdc.functions.balanceOf(self.address).call()
                approve_tx = usdc.functions.approve(config["DEX_ROUTER"], balance).build_transaction({
                    'from': self.address,
                    'gas': 60000,
                    'gasPrice': self.web3.to_wei('5', 'gwei'),
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                    'chainId': config["CHAIN_ID"]
                })
                signed_approve = self.account.sign_transaction(approve_tx)
                approve_hash = self.web3.eth.send_raw_transaction(signed_approve.rawTransaction)
                print(f"✅ Aprovação enviada: {self.web3.to_hex(approve_hash)}")
                return

            tx = self.router.functions.swapExactTokensForETH(
                amount_in,
                0,
                [config["USDC"], config["WETH"]],
                self.address,
                int(self.web3.eth.get_block('latest')['timestamp']) + config["TX_DEADLINE_SEC"]
            ).build_transaction({
                'from': self.address,
                'gas': 250000,
                'gasPrice': self.web3.to_wei('5', 'gwei'),
                'nonce': self.web3.eth.get_transaction_count(self.address),
                'chainId': config["CHAIN_ID"]
            })

            signed_tx = self.account.sign_transaction(tx)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            print(f"✅ Venda enviada: {self.web3.to_hex(tx_hash)}")

        except Exception as e:
            print(f"❌ Erro ao vender: {e}")
