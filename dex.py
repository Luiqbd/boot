import os
import json
import logging
from web3 import Web3
from config import config

logger = logging.getLogger(__name__)

class DexClient:
    def __init__(self, web3):
        self.web3 = web3

        # Carrega ABI do Uniswap Router
        abi_path = os.path.join(os.path.dirname(__file__), "abis", "uniswap_router.json")
        with open(abi_path) as f:
            router_abi = json.load(f)

        # Corrige checksum do endereço do contrato
        raw_address = config["DEX_ROUTER"]
        router_address = Web3.to_checksum_address(raw_address)

        self.router = web3.eth.contract(address=router_address, abi=router_abi)

    def get_token_price(self, token_address):
        """Simula o preço do token. Em produção, usar reserves ou Dex Screener."""
        try:
            return 0.000123  # Simulação
        except Exception as e:
            logger.error(f"Erro ao obter preço do token: {e}")
            return None

    def swap_exact_eth_for_tokens(self, token_address, amount_eth, recipient_address):
        """Executa swap real via Uniswap Router."""
        try:
            path = [
                Web3.to_checksum_address(config["WETH"]),
                Web3.to_checksum_address(token_address)
            ]
            deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + config.get("TX_DEADLINE_SEC", 300)
            amount_out_min = 0  # Pode ser ajustado com slippage

            tx = self.router.functions.swapExactETHForTokens(
                amount_out_min,
                path,
                recipient_address,
                deadline
            ).build_transaction({
                "from": recipient_address,
                "value": self.web3.to_wei(amount_eth, "ether"),
                "gas": 250000,
                "gasPrice": self.web3.eth.gas_price,
                "nonce": self.web3.eth.get_transaction_count(recipient_address)
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, config["PRIVATE_KEY"])
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"Swap enviado: {tx_hash.hex()}")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Erro ao executar swap: {e}")
            return None

    def is_honeypot(self, token_address):
        """Verifica se o token pode ser vendido (anti-honeypot)."""
        try:
            path = [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(config["WETH"])
            ]
            amount_in = self.web3.to_wei(0.001, "ether")
            self.router.functions.getAmountsOut(amount_in, path).call()
            return False  # Não é honeypot
        except Exception as e:
            logger.warning(f"[HONEYPOT] Token {token_address} falhou na simulação de venda.")
            return True

    def has_min_liquidity(self, token_address):
        """Verifica se o par tem liquidez mínima em WETH."""
        try:
            path = [
                Web3.to_checksum_address(config["WETH"]),
                Web3.to_checksum_address(token_address)
            ]
            amount_in = self.web3.to_wei(1, "ether")
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
            received = self.web3.fromWei(amounts[-1], "ether")
            min_liq = float(config.get("MIN_LIQ_WETH", 0.5))
            if received < min_liq:
                logger.warning(f"[LIQUIDEZ BAIXA] Token {token_address} retorna apenas {received:.4f} tokens por 1 WETH.")
                return False
            return True
        except Exception as e:
            logger.error(f"Erro ao verificar liquidez: {e}")
            return False
