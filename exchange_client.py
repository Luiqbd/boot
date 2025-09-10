# exchange_client.py

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eth_account import Account
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput

from config import config

logger = logging.getLogger(__name__)

def _codigo_vazio(codigo: bytes) -> bool:
    """Retorna True se não houver bytecode no endereço (contrato não implantado)."""
    return codigo is None or len(codigo) == 0

class ExchangeClient:
    """
    Cliente para interagir com routers Uniswap/PancakeSwap (v2/v3).

    Funcionalidades:
      - swap exato ETH→token e token→ETH
      - approval condicional de token
      - cálculo de slippage (bps) e deadlines
      - dry-run para testes (TX fake)
      - cache interno de decimals e allowance
    """

    _router_abi: Dict[str, Any] = {}
    _erc20_abi: Dict[str, Any] = {}
    _abis_carregados = False

    def __init__(self, router_address: str):
        # Conexão Web3
        self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        # corrigido para snake_case
        if not self.web3.is_connected():
            raise ConnectionError(f"Não conectado a {config['RPC_URL']}")

        # Conta e carteira
        self.account = Account.from_key(config["PRIVATE_KEY"])
        self.wallet = self.account.address

        # Validação opcional de WALLET_ADDRESS
        env_wallet = config.get("WALLET_ADDRESS", "").strip()
        if env_wallet:
            chk = Web3.to_checksum_address(env_wallet)
            if chk != Web3.to_checksum_address(self.wallet):
                raise ValueError("WALLET_ADDRESS diferente da PRIVATE_KEY")

        # Endereço do router em checksum
        self.router_address = Web3.to_checksum_address(router_address)
        code = self.web3.eth.get_code(self.router_address)
        if _codigo_vazio(code):
            raise ValueError(f"Router {self.router_address} não implantado nesta rede")

        # Carrega ABIs na primeira instância
        if not ExchangeClient._abis_carregados:
            base = Path(__file__).parent / "abis"
            with open(base / "uniswap_router.json") as f:
                ExchangeClient._router_abi = json.load(f)
            with open(base / "erc20.json") as f:
                ExchangeClient._erc20_abi = json.load(f)
            ExchangeClient._abis_carregados = True

        # Contrato do router
        self.router = self.web3.eth.contract(
            address=self.router_address,
            abi=ExchangeClient._router_abi
        )

        # Cache interno
        self._decimals_cache: Dict[str, Tuple[int, float]] = {}
        self._allowance_cache: Dict[Tuple[str, str], Tuple[int, float]] = {}
        self._cache_ttl = int(config.get("CACHE_TTL_SEC", 300))

    def _parametros_gas(self) -> Dict[str, int]:
        """Calcula maxFeePerGas e maxPriorityFeePerGas."""
        base_fee = self.web3.eth.gas_price
        return {
            "maxFeePerGas": int(base_fee * 2),
            "maxPriorityFeePerGas": int(base_fee * 0.1),
        }

    def _nonce(self) -> int:
        """Retorna nonce pendente para a carteira."""
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _construir_tx(
        self,
        fn_call,
        overrides: Dict[str, Any],
        gas_padrao: int
    ) -> Dict[str, Any]:
        """
        Constrói e estima gas para a transação.
        Usa gas_padrao se estimate_gas falhar.
        """
        tx = fn_call.build_transaction(overrides)
        try:
            estimado = self.web3.eth.estimate_gas(tx)
            tx["gas"] = int(estimado * 1.2)
        except Exception as e:
            logger.warning(f"Estimativa de gas falhou, usando {gas_padrao}: {e}")
            tx["gas"] = gas_padrao
        return tx

    def _assinar_enviar(self, tx: Dict[str, Any]) -> str:
        """Assina e envia a transação, retorna tx_hash hex."""
        signed = self.web3.eth.account.sign_transaction(tx, self.account.key)
        txh = self.web3.eth.send_raw_transaction(signed.rawTransaction)
        return self.web3.to_hex(txh)

    def get_token_decimals(self, token_address: str) -> int:
        """
        Retorna decimais de um token ERC20, com cache TTL.
        """
        addr = Web3.to_checksum_address(token_address)
        agora = time.time()

        # Retorna cache se válido
        if addr in self._decimals_cache:
            dec, ts = self._decimals_cache[addr]
            if agora - ts < self._cache_ttl:
                return dec

        # Consulta on-chain ou assume 18
        try:
            token = self.web3.eth.contract(address=addr, abi=ExchangeClient._erc20_abi)
            dec = token.functions.decimals().call()
        except (BadFunctionCallOutput, Exception) as e:
            logger.warning(f"Erro ao obter decimals de {addr}: {e}; assumindo 18")
            dec = 18

        self._decimals_cache[addr] = (int(dec), agora)
        return int(dec)

    def approve_token(self, token_address: str, amount: int) -> str:
        """
        Executa approve se allowance < amount.
        Retorna tx_hash, '0xALLOWOK' se já aprovado ou fake se dry-run.
        """
        addr = Web3.to_checksum_address(token_address)
        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] approve_token ignorado")
            return "0xDRYRUN"

        agora = time.time()
        key = (addr, self.router_address)

        # Consulta allowance com cache
        if key in self._allowance_cache:
            allowance, ts = self._allowance_cache[key]
            if agora - ts < self._cache_ttl:
                current = allowance
            else:
                current = None
        else:
            current = None

        if current is None:
            token = self.web3.eth.contract(address=addr, abi=ExchangeClient._erc20_abi)
            current = token.functions.allowance(self.wallet, self.router_address).call()
            self._allowance_cache[key] = (current, agora)

        if current >= amount:
            return "0xALLOWOK"

        fn = token.functions.approve(self.router_address, amount)
        tx = self._construir_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "chainId": int(config["CHAIN_ID"]),
                **self._parametros_gas(),
                "nonce": self._nonce(),
            },
            gas_padrao=120_000
        )
        return self._assinar_enviar(tx)

    def _calcular_amount_out_min(
        self,
        amount_in: int,
        path: List[str],
        slippage_bps: Optional[int]
    ) -> Tuple[int, int]:
        """
        Consulta getAmountsOut e aplica slippage.
        Retorna (amountOutMin, expectedOut).
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
        Swap ETH → token. Calcula amountOutMin se não informado.
        Retorna tx_hash ou fake se dry-run.
        """
        path = [
            Web3.to_checksum_address(token_in_weth),
            Web3.to_checksum_address(token_out),
        ]

        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._calcular_amount_out_min(amount_in_wei, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] buy_token ignorado")
            return "0xDRYRUN"

        deadline = self.web3.eth.get_block("latest")["timestamp"] + int(config.get("TX_DEADLINE_SEC", 300))
        fn = self.router.functions.swapExactETHForTokens(
            amount_out_min, path, self.wallet, deadline
        )
        tx = self._construir_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "value": amount_in_wei,
                "chainId": int(config["CHAIN_ID"]),
                **self._parametros_gas(),
                "nonce": self._nonce(),
            },
            gas_padrao=350_000
        )
        return self._assinar_enviar(tx)

    def sell_token(
        self,
        token_in: str,
        token_out_weth: str,
        amount_in: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> str:
        """
        Swap token → ETH. Faz approve se necessário.
        Retorna tx_hash ou fake se dry-run.
        """
        path = [
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out_weth),
        ]

        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._calcular_amount_out_min(amount_in, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] sell_token ignorado")
            return "0xDRYRUN"

        # Garantir approval antes de vender
        self.approve_token(token_in, amount_in)

        deadline = self.web3.eth.get_block("latest")["timestamp"] + int(config.get("TX_DEADLINE_SEC", 300))
        fn = self.router.functions.swapExactTokensForETH(
            amount_in, amount_out_min, path, self.wallet, deadline
        )
        tx = self._construir_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "chainId": int(config["CHAIN_ID"]),
                **self._parametros_gas(),
                "nonce": self._nonce(),
            },
            gas_padrao=400_000
        )
        return self._assinar_enviar(tx)
