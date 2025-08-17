import os
import json
import logging
from web3 import Web3

logger = logging.getLogger(__name__)

class DexClient:
    def __init__(self, web3, router_address):
        """
        :param web3: instância Web3 já conectada à rede
        :param router_address: endereço do router da DEX (string)
        """
        self.web3 = web3

        # Carrega ABI do Uniswap Router (padrão)
        abi_path = os.path.join(os.path.dirname(__file__), "abis", "uniswap_router.json")
        with open(abi_path) as f:
            router_abi = json.load(f)

        # Corrige checksum do endereço do contrato
        router_address = Web3.to_checksum_address(router_address)

        # (Opcional) Validação do contrato
        # code = web3.eth.get_code(router_address)
        # if code == b'0x':
        #     raise ValueError(f"Contrato inválido ou inexistente: {router_address}")

        self.router = web3.eth.contract(address=router_address, abi=router_abi)

    def get_token_price(self, token_address):
        """Simula o preço do token. Em produção, usar reserves ou API externa."""
        try:
            return 0.000123  # Simulação
        except Exception as e:
            logger.error(f"Erro ao obter preço do token: {e}")
            return None

    def swap_exact_eth_for_tokens(self, token_address, amount_eth, recipient_address, weth_address, private_key, slippage_bps, tx_deadline_sec):
        """Executa swap via Uniswap Router com proteção de slippage."""
        try:
            path = [
                Web3.to_checksum_address(weth_address),
                Web3.to_checksum_address(token_address)
            ]
            deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + tx_deadline_sec

            # Calcula amountOutMin com slippage
            amount_in_wei = self.web3.to_wei(amount_eth, "ether")
            expected_out = self.router.functions.getAmountsOut(amount_in_wei, path).call()[-1]
            slippage = slippage_bps / 10000  # ex: 1200 → 0.12
            amount_out_min = int(expected_out * (1 - slippage))

            tx = self.router.functions.swapExactETHForTokens(
                amount_out_min,
                path,
                recipient_address,
                deadline
            ).build_transaction({
                "from": recipient_address,
                "value": amount_in_wei,
                "gas": 250000,
                "gasPrice": self.web3.eth.gas_price,
                "nonce": self.web3.eth.get_transaction_count(recipient_address)
            })

            signed_tx = self.web3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
            logger.info(f"✅ Swap enviado: {tx_hash.hex()} | Slippage: {slippage*100:.2f}% | MinOut: {amount_out_min}")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"❌ Erro ao executar swap: {e}")
            return None

    def is_honeypot(self, token_address, weth_address):
        """Verifica se o token pode ser vendido (anti-honeypot)."""
        try:
            path = [
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(weth_address)
            ]
            amount_in = self.web3.to_wei(0.001, "ether")
            self.router.functions.getAmountsOut(amount_in, path).call()
            return False
        except Exception:
            logger.warning(f"[HONEYPOT] Token {token_address} falhou na simulação de venda.")
            return True

    def has_min_liquidity(self, token_address, weth_address, min_liq_weth=0.5):
        """Verifica se o par tem liquidez mínima em WETH."""
        try:
            path = [
                Web3.to_checksum_address(weth_address),
                Web3.to_checksum_address(token_address)
            ]
            amount_in = self.web3.to_wei(1, "ether")
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
            received = self.web3.fromWei(amounts[-1], "ether")
            if received < min_liq_weth:
                logger.warning(f"[LIQUIDEZ BAIXA] Token {token_address} retorna apenas {received:.4f} tokens por 1 WETH.")
                return False
            return True
        except Exception as e:
            logger.error(f"Erro ao verificar liquidez: {e}")
            return False
