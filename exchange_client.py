# exchange_client.py

import json
import logging
import time
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

from web3 import Web3
from eth_account import Account

from config import config

logger = logging.getLogger(__name__)


def _is_empty_code(code: bytes) -> bool:
    """Retorna True se não houver bytecode no endereço (contrato não implantado)."""
    return code is None or len(code) == 0


class ExchangeClient:
    """
    Cliente para interagir com routers Uniswap/PancakeSwap (v2/v3).
    Suporta approval de token, swaps ETH→token e token→ETH, controle de slippage
    e dry-run para testes sem enviar transações reais.
    """

    def __init__(self, router_address: str):
        # Inicializa conexão Web3 e conta
        self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        self.account = Account.from_key(config["PRIVATE_KEY"])
        self.wallet = self.account.address

        # Validação opcional de WALLET_ADDRESS
        env_wallet = config.get("WALLET_ADDRESS", "").strip()
        if env_wallet:
            chk = Web3.to_checksum_address(env_wallet)
            if chk != Web3.to_checksum_address(self.wallet):
                raise ValueError("WALLET_ADDRESS difere da PRIVATE_KEY")

        # Verifica implantação do router on-chain
        self.router_address = Web3.to_checksum_address(router_address)
        code = self.web3.eth.get_code(self.router_address)
        if _is_empty_code(code):
            raise ValueError(f"Router {self.router_address} não implantado nesta rede")

        # Carrega ABIs
        with open("abis/uniswap_router.json", "r") as f:
            self.router_abi = json.load(f)
        with open("abis/erc20.json", "r") as f:
            self.erc20_abi = json.load(f)

        self.router = self.web3.eth.contract(
            address=self.router_address,
            abi=self.router_abi
        )
        # Cache interno para decimals de tokens
        self._decimals_cache: Dict[str, int] = {}

    def _gas_params(self) -> Dict[str, int]:
        base_fee = int(self.web3.eth.gas_price)
        return {
            "maxFeePerGas": int(base_fee * 2),
            "maxPriorityFeePerGas": int(base_fee * 0.1),
        }

    def _nonce(self) -> int:
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _build_tx(
        self,
        fn_call,
        tx_overrides: dict,
        default_gas: int
    ) -> dict:
        """
        Constrói transação, estimando gas ou usando valor padrão.
        """
        tx = fn_call.build_transaction(tx_overrides)
        try:
            estimated = self.web3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated * 1.2)
        except Exception as e:
            logger.warning(f"Estimativa de gas falhou, usando {default_gas}: {e}")
            tx["gas"] = default_gas
        return tx

    def _sign_and_send(self, tx: dict) -> str:
        signed = self.web3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed.rawTransaction)
        return self.web3.to_hex(tx_hash)

    def get_token_decimals(self, token_address: str) -> int:
        """
        Retorna o número de decimais de um token ERC20, com cache interno.
        """
        token_address = Web3.to_checksum_address(token_address)
        if token_address in self._decimals_cache:
            return self._decimals_cache[token_address]

        try:
            token = self.web3.eth.contract(
                address=token_address,
                abi=self.erc20_abi
            )
            decimals = token.functions.decimals().call()
        except Exception as e:
            logger.warning(f"Não foi possível obter decimals de {token_address}; assumindo 18: {e}")
            decimals = 18

        self._decimals_cache[token_address] = int(decimals)
        return int(decimals)

    def approve_token(self, token_address: str, amount: int) -> str:
        """
        Envia transação de approve se a allowance for menor que o valor desejado.
        """
        token_address = Web3.to_checksum_address(token_address)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] approve_token skip")
            return "0xDRYRUN"

        token = self.web3.eth.contract(address=token_address, abi=self.erc20_abi)
        allowance = token.functions.allowance(self.wallet, self.router_address).call()
        if allowance >= amount:
            return "0xALLOWOK"

        fn = token.functions.approve(self.router_address, amount)
        tx = self._build_tx(
            fn_call=fn,
            tx_overrides={
                "from": self.wallet,
                "chainId": int(config["CHAIN_ID"]),
                **self._gas_params(),
                "nonce": self._nonce(),
            },
            default_gas=120_000
        )
        return self._sign_and_send(tx)

    def _amount_out_min(
        self,
        amount_in: int,
        path: List[str],
        slippage_bps: Optional[int]
    ) -> Tuple[int, int]:
        """
        Consulta getAmountsOut e aplica slippage (bps) para definir amountOutMin.
        Retorna (amountOutMin, expected).
        """
        try:
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
        except Exception as e:
            raise RuntimeError(f"getAmountsOut falhou: {e}")

        expected = int(amounts[-1])
        bps = slippage_bps if slippage_bps is not None else int(config["DEFAULT_SLIPPAGE_BPS"])
        amount_out_min = int(expected * (1 - bps / 10_000))
        if amount_out_min <= 0:
            raise ValueError("amountOutMin calculado <= 0")
        return amount_out_min, expected

    def buy_token(
        self,
        token_in_weth: str,
        token_out: str,
        amount_in_wei: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> str:
        """
        Swap ETH → token. Se amount_out_min não informado, calcula com getAmountsOut.
        """
        path = [Web3.to_checksum_address(token_in_weth),
                Web3.to_checksum_address(token_out)]

        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._amount_out_min(amount_in_wei, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] buy_token skip")
            return "0xDRYRUN"

        deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + \
                   int(config.get("TX_DEADLINE_SEC", 300))

        fn = self.router.functions.swapExactETHForTokens(
            amount_out_min, path, self.wallet, deadline
        )
        tx = self._build_tx(
            fn_call=fn,
            tx_overrides={
                "from": self.wallet,
                "value": amount_in_wei,
                "chainId": int(config["CHAIN_ID"]),
                **self._gas_params(),
                "nonce": self._nonce(),
            },
            default_gas=350_000
        )
        return self._sign_and_send(tx)

    def sell_token(
        self,
        token_in: str,
        token_out_weth: str,
        amount_in: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> str:
        """
        Swap token → ETH. Faz approve se necessário e calcula amountOutMin se não informado.
        """
        path = [Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out_weth)]

        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._amount_out_min(amount_in, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] sell_token skip")
            return "0xDRYRUN"

        # Garante aprovação
        self.approve_token(token_in, amount_in)

        deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + \
                   int(config.get("TX_DEADLINE_SEC", 300))

        fn = self.router.functions.swapExactTokensForETH(
            amount_in, amount_out_min, path, self.wallet, deadline
        )
        tx = self._build_tx(
            fn_call=fn,
            tx_overrides={
                "from": self.wallet,
                "chainId": int(config["CHAIN_ID"]),
                **self._gas_params(),
                "nonce": self._nonce(),
            },
            default_gas=400_000
        )
        return self._sign_and_send(tx)
