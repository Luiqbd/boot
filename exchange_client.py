# exchange_client.py

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from eth_account import Account
    from web3 import Web3
    from web3.exceptions import BadFunctionCallOutput
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Account = None
    Web3 = None
    BadFunctionCallOutput = Exception

from config import config

logger = logging.getLogger(__name__)

def _codigo_vazio(codigo: bytes) -> bool:
    return codigo is None or len(codigo) == 0

class ExchangeClient:
    """
    Swap ETH↔token via Uniswap/PancakeSwap routers (v2/v3).
    """

    _router_abi: List[Dict[str, Any]] = []
    _erc20_abi:   List[Dict[str, Any]] = []
    _abis_carregados = False

    def __init__(self, router_address: str):
        self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        if not self.web3.is_connected():
            raise ConnectionError(f"RPC não conectado: {config['RPC_URL']}")

        self.account = Account.from_key(config["PRIVATE_KEY"])
        self.wallet  = self.account.address

        # valida parâmetro opcional WALLET_ADDRESS
        env_wallet = config.get("WALLET_ADDRESS", "")
        if env_wallet:
            chk = Web3.to_checksum_address(env_wallet)
            if chk != self.wallet:
                raise ValueError("WALLET_ADDRESS ≠ PRIVATE_KEY")

        self.router_address = Web3.to_checksum_address(router_address)
        code = self.web3.eth.get_code(self.router_address)
        if _codigo_vazio(code):
            raise ValueError(f"Router não implantado: {self.router_address}")

        if not ExchangeClient._abis_carregados:
            base = Path(__file__).parent / "abis"
            with open(base / "uniswap_router.json") as f:
                ExchangeClient._router_abi = json.load(f)
            with open(base / "erc20.json") as f:
                ExchangeClient._erc20_abi = json.load(f)
            ExchangeClient._abis_carregados = True

        self.router = self.web3.eth.contract(
            address=self.router_address,
            abi=ExchangeClient._router_abi
        )
        self._dec_cache: Dict[str, Tuple[int,float]] = {}
        self._allow_cache: Dict[Tuple[str,str], Tuple[int,float]] = {}
        self._ttl = int(config.get("CACHE_TTL_SEC", 300))

    def _param_gas(self) -> Dict[str,int]:
        base_fee = self.web3.eth.gas_price
        return {
            "maxFeePerGas": int(base_fee * 2),
            "maxPriorityFeePerGas": int(base_fee * 0.1)
        }

    def _nonce(self) -> int:
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _build_tx(self, fn_call, overrides: Dict[str,Any], default_gas: int):
        tx = fn_call.build_transaction(overrides)
        try:
            est = self.web3.eth.estimate_gas(tx)
            tx["gas"] = int(est * 1.2)
        except Exception:
            tx["gas"] = default_gas
        return tx

    def _sign_send(self, tx: Dict[str,Any]) -> str:
        signed = self.web3.eth.account.sign_transaction(tx, self.account.key)
        h = self.web3.eth.send_raw_transaction(signed.rawTransaction)
        return self.web3.to_hex(h)

    def get_decimals(self, token_address: str) -> int:
        addr = Web3.to_checksum_address(token_address)
        now = time.time()
        if addr in self._dec_cache:
            dec, ts = self._dec_cache[addr]
            if now - ts < self._ttl:
                return dec
        try:
            token = self.web3.eth.contract(addr, abi=ExchangeClient._erc20_abi)
            dec = token.functions.decimals().call()
        except BadFunctionCallOutput:
            dec = 18
        self._dec_cache[addr] = (int(dec), now)
        return int(dec)

    def approve_token(self, token_address: str, amount: int) -> str:
        addr = Web3.to_checksum_address(token_address)
        if config["DRY_RUN"]:
            logger.info("[DRY_RUN] approve ignorado")
            return "0xDRYRUN"
        key = (addr, self.router_address)
        now = time.time()
        if key in self._allow_cache:
            cur, ts = self._allow_cache[key]
            if now - ts < self._ttl:
                current = cur
            else:
                current = None
        else:
            current = None
        if current is None:
            token = self.web3.eth.contract(addr, abi=ExchangeClient._erc20_abi)
            current = token.functions.allowance(self.wallet, self.router_address).call()
            self._allow_cache[key] = (current, now)
        if current >= amount:
            return "0xALLOWOK"
        token = self.web3.eth.contract(addr, abi=ExchangeClient._erc20_abi)
        fn = token.functions.approve(self.router_address, amount)
        tx = self._build_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "chainId": config["CHAIN_ID"],
                **self._param_gas(),
                "nonce": self._nonce()
            },
            default_gas=120_000
        )
        return self._sign_send(tx)

    def _calc_amounts(
        self, amount_in: int, path: List[str], slippage_bps: Optional[int]
    ) -> Tuple[int,int]:
        amounts = self.router.functions.getAmountsOut(amount_in, path).call()
        expected = amounts[-1]
        bps = slippage_bps if slippage_bps is not None else config["SLIPPAGE_BPS"]
        min_out = int(expected * (1 - bps/10_000))
        if min_out <= 0:
            raise ValueError("amountOutMin ≤ 0")
        return min_out, expected

    def buy_token(
        self,
        token_in_weth: str,
        token_out: str,
        amount_in_wei: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> str:
        path = [
            Web3.to_checksum_address(token_in_weth),
            Web3.to_checksum_address(token_out),
        ]
        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._calc_amounts(amount_in_wei, path, slippage_bps)
        if config["DRY_RUN"]:
            logger.info("[DRY_RUN] buy ignorado")
            return "0xDRYRUN"
        deadline = self.web3.eth.get_block("latest")["timestamp"] + config["TX_DEADLINE_SEC"]
        fn = self.router.functions.swapExactETHForTokens(
            amount_out_min, path, self.wallet, deadline
        )
        tx = self._build_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "value": amount_in_wei,
                "chainId": config["CHAIN_ID"],
                **self._param_gas(),
                "nonce": self._nonce()
            },
            default_gas=350_000
        )
        return self._sign_send(tx)

    def sell_token(
        self,
        token_in: str,
        token_out_weth: str,
        amount_in: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> str:
        path = [
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out_weth),
        ]
        if not amount_out_min or amount_out_min <= 0:
            amount_out_min, _ = self._calc_amounts(amount_in, path, slippage_bps)
        if config["DRY_RUN"]:
            logger.info("[DRY_RUN] sell ignorado")
            return "0xDRYRUN"
        self.approve_token(token_in, amount_in)
        deadline = self.web3.eth.get_block("latest")["timestamp"] + config["TX_DEADLINE_SEC"]
        fn = self.router.functions.swapExactTokensForETH(
            amount_in, amount_out_min, path, self.wallet, deadline
        )
        tx = self._build_tx(
            fn_call=fn,
            overrides={
                "from": self.wallet,
                "chainId": config["CHAIN_ID"],
                **self._param_gas(),
                "nonce": self._nonce()
            },
            default_gas=400_000
        )
        return self._sign_send(tx)
