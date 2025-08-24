import os
import json
import logging
from web3 import Web3
from config import config  # para gas, deadline e slippage vindos do ambiente

logger = logging.getLogger(__name__)

# ABI mínima Uniswap V2
V2_PAIR_ABI = [
    {
        "name": "getReserves",
        "outputs": [
            {"type": "uint112", "name": "_reserve0"},
            {"type": "uint112", "name": "_reserve1"},
            {"type": "uint32", "name": "_blockTimestampLast"}
        ],
        "inputs": [],
        "stateMutability": "view",
        "type": "function"
    }
]

# ABI mínima Uniswap V3
V3_POOL_ABI = [
    {
        "name": "liquidity",
        "outputs": [{"type": "uint128", "name": ""}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function"
    }
]

class DexClient:
    def __init__(self, web3, router_address):
        """
        :param web3: instância Web3 já conectada à rede
        :param router_address: endereço do router da DEX (string)
        """
        self.web3 = web3

        # Carrega ABI do Uniswap Router
        abi_path = os.path.join(os.path.dirname(__file__), "abis", "uniswap_router.json")
        with open(abi_path) as f:
            router_abi = json.load(f)

        self.router = web3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=router_abi
        )

    def detect_version(self, pair_address: str) -> str:
        """Detecta se o par/pool é V2 ou V3."""
        pair = self.web3.eth.contract(address=Web3.to_checksum_address(pair_address))
        try:
            pair.get_function_by_name("getReserves")
            return "v2"
        except ValueError:
            try:
                pair.get_function_by_name("liquidity")
                return "v3"
            except ValueError:
                return "unknown"

    def get_token_price(self, token_address, weth_address):
        """Retorna o preço do token em WETH (quanto 1 TOKEN vale em WETH)."""
        try:
            amt_in = self.web3.to_wei(1, "ether")
            path = [Web3.to_checksum_address(token_address), Web3.to_checksum_address(weth_address)]
            out = self.router.functions.getAmountsOut(amt_in, path).call()[-1]
            price_weth = out / 1e18
            logger.debug(f"[PREÇO] 1 {token_address} = {price_weth} WETH")
            return price_weth
        except Exception as e:
            logger.error(f"Erro ao obter preço do token {token_address}: {e}")
            return None

    def swap_exact_eth_for_tokens(
        self, token_address, amount_eth, recipient_address, weth_address,
        private_key, slippage_bps=None, tx_deadline_sec=None, gas_limit=None
    ):
        """Executa swap via Uniswap Router com proteção de slippage."""
        try:
            path = [
                Web3.to_checksum_address(weth_address),
                Web3.to_checksum_address(token_address)
            ]
            deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + (tx_deadline_sec or config.get("TX_DEADLINE_SEC", 45))

            amount_in_wei = self.web3.to_wei(amount_eth, "ether")
            expected_out = self.router.functions.getAmountsOut(amount_in_wei, path).call()[-1]
            slip_bps = slippage_bps or config.get("DEFAULT_SLIPPAGE_BPS", 1200)
            slippage = slip_bps / 10000
            amount_out_min = int(expected_out * (1 - slippage))

            tx = self.router.functions.swapExactETHForTokens(
                amount_out_min,
                path,
                recipient_address,
                deadline
            ).build_transaction({
                "from": recipient_address,
                "value": amount_in_wei,
                "gas": gas_limit or 250000,
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
        """Verifica se o token pode ser vendido (anti-honeypot simples)."""
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

    def has_min_liquidity(self, pair_address, weth_address, min_liq_weth=0.5):
        """
        Verifica se o par/pool tem pelo menos min_liq_weth de liquidez.
        Adapta para Uniswap V2 ou V3 automaticamente.
        """
        version = self.detect_version(pair_address)
        try:
            if version == "v2":
                pair = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V2_PAIR_ABI
                )
                reserves = pair.functions.getReserves().call()
                reserve_weth = max(reserves[0], reserves[1]) / (10 ** 18)
                logger.debug(f"[V2] Liquidez WETH detectada: {reserve_weth}")
                return reserve_weth >= min_liq_weth

            elif version == "v3":
                pool = self.web3.eth.contract(
                    address=Web3.to_checksum_address(pair_address),
                    abi=V3_POOL_ABI
                )
                liq = pool.functions.liquidity().call()
                reserve_weth_equiv = liq / (10 ** 18)
                logger.debug(f"[V3] Liquidez equivalente WETH detectada: {reserve_weth_equiv}")
                return reserve_weth_equiv >= min_liq_weth

            else:
                logger.warning(f"⚠️ Tipo de par desconhecido: {pair_address} — pulando verificação de liquidez")
                return False

        except Exception as e:
            logger.error(f"Erro ao verificar liquidez ({version.upper()}): {e}", exc_info=True)
            return False
