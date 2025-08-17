import json
from decimal import Decimal
from web3 import Web3
from eth_account import Account

from config import config  # Importa configurações do seu config.py

WETH = Web3.to_checksum_address(config["WETH"])


def _to_wei_eth(web3, amount_eth):
    """Converte valor em ETH para Wei, usando Decimal para evitar perda de precisão."""
    return web3.to_wei(Decimal(str(amount_eth)), "ether")


def _is_empty_code(code) -> bool:
    """Retorna True se o contrato não estiver implantado (bytecode vazio)."""
    return code is None or len(code) == 0


class ExchangeClient:
    def __init__(self):
        # Conexão Web3
        self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        self.private_key = config["PRIVATE_KEY"]
        self.wallet = Account.from_key(self.private_key).address

        # Validação de endereço opcional
        env_wallet = (config.get("WALLET_ADDRESS") or "").strip()
        if env_wallet:
            if Web3.to_checksum_address(env_wallet) != Web3.to_checksum_address(self.wallet):
                raise ValueError("WALLET_ADDRESS difere do endereço derivado da PRIVATE_KEY")

        # Checa contrato do roteador
        self.router_address = Web3.to_checksum_address(config["DEX_ROUTER"])
        code = self.web3.eth.get_code(self.router_address)
        if _is_empty_code(code):
            raise ValueError(f"Roteador {self.router_address} não implantado")

        # Carrega ABIs do repositório abis/
        with open("abis/uniswap_router.json") as f:
            self.router_abi = json.load(f)
        with open("abis/erc20.json") as f:
            self.erc20_abi = json.load(f)

        # Instancia contrato do roteador
        self.router = self.web3.eth.contract(address=self.router_address, abi=self.router_abi)

    def _gas_params(self):
        base_fee = self.web3.eth.gas_price
        return {
            "maxFeePerGas": int(base_fee * 2),
            "maxPriorityFeePerGas": int(base_fee * 0.1),
        }

    def _nonce(self):
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _amount_out_min(self, amount_in_wei, path):
        amounts = self.router.functions.getAmountsOut(amount_in_wei, path).call()
        expected = amounts[-1]
        bps = int(config["DEFAULT_SLIPPAGE_BPS"])
        min_out = int(expected * (1 - bps / 10_000))
        if min_out <= 0:
            raise ValueError("amountOutMin calculado <= 0")
        return min_out, expected

    def approve_token(self, token_address, amount_base_units):
        token_address = Web3.to_checksum_address(token_address)

        if config.get("DRY_RUN"):
            return "0xDRYRUN"

        token = self.web3.eth.contract(address=token_address, abi=self.erc20_abi)
        allowance = token.functions.allowance(self.wallet, self.router_address).call()
        if allowance >= amount_base_units:
            return "0xALLOWOK"

        tx = token.functions.approve(self.router_address, amount_base_units).build_transaction({
            "from": self.wallet,
            **self._gas_params(),
            "nonce": self._nonce(),
            "chainId": config["CHAIN_ID"],
        })
        tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
        return self.web3.to_hex(self.web3.eth.send_raw_transaction(signed.rawTransaction))

    def buy_token(self, token_in_weth, token_out, amount_in_wei, amount_out_min=None):
        token_out = Web3.to_checksum_address(token_out)
        path = [WETH, token_out]

        if amount_out_min in (None, 0):
            amount_out_min, _ = self._amount_out_min(amount_in_wei, path)

        if config.get("DRY_RUN"):
            return "0xDRYRUN"

        deadline = self.web3.eth.get_block("latest")["timestamp"] + config.get("TX_DEADLINE_SEC", 300)
        tx = self.router.functions.swapExactETHForTokens(
            amount_out_min, path, self.wallet, deadline
        ).build_transaction({
            "from": self.wallet,
            "value": amount_in_wei,
            **self._gas_params(),
            "nonce": self._nonce(),
            "chainId": config["CHAIN_ID"],
        })
        tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
        return self.web3.to_hex(self.web3.eth.send_raw_transaction(signed.rawTransaction))

    def sell_token(self, token_in, token_out_weth, amount_in_base_units, amount_out_min=None):
        token_in = Web3.to_checksum_address(token_in)
        path = [token_in, WETH]

        if amount_out_min in (None, 0):
            amount_out_min, _ = self._amount_out_min(amount_in_base_units, path)

        if config.get("DRY_RUN"):
            return "0xDRYRUN"

        self.approve_token(token_in, amount_in_base_units)

        deadline = self.web3.eth.get_block("latest")["timestamp"] + config.get("TX_DEADLINE_SEC", 300)
        tx = self.router.functions.swapExactTokensForETH(
            amount_in_base_units, amount_out_min, path, self.wallet, deadline
        ).build_transaction({
            "from": self.wallet,
            **self._gas_params(),
            "nonce": self._nonce(),
            "chainId": config["CHAIN_ID"],
        })
        tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
        return self.web3.to_hex(self.web3.eth.send_raw_transaction(signed.rawTransaction))
