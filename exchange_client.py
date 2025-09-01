import json
import logging
import time
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple, Union

from eth_account import Account
from telegram import Bot
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from config import config
from telegram_alert import send_report

logger = logging.getLogger(__name__)


class ExchangeClient:
    """
    Cliente para interação com um router DEX (Uniswap, PancakeSwap etc.).
    
    - faz swap ETH↔ERC20
    - aprova tokens
    - calcula slippage, deadline e gas
    - dispara alertas via Telegram
    """

    def __init__(
        self,
        router_address: str,
        rpc_url: str = config["RPC_URL"],
        private_key: str = config["PRIVATE_KEY"],
        telegram_token: str = config["TELEGRAM_TOKEN"],
        chain_id: int = int(config["CHAIN_ID"]),
        default_slippage_bps: int = int(config.get("DEFAULT_SLIPPAGE_BPS", 50)),
        tx_deadline_sec: int = int(config.get("TX_DEADLINE_SEC", 300)),
    ) -> None:
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        self.chain_id = chain_id
        self.wallet = Account.from_key(private_key).address
        self.private_key = private_key
        self.bot = Bot(token=telegram_token)
        self.default_slippage = default_slippage_bps
        self.tx_deadline = tx_deadline_sec

        # valida router
        self.router_address = self._to_checksum(router_address)
        if not self._has_code(self.router_address):
            raise ValueError(f"Router não encontrado em {self.router_address}")

        # carrega ABIs  
        base = Path(__file__).parent / "abis"
        self.erc20_abi = json.loads((base / "erc20.json").read_text())
        self.router_abi = json.loads((base / "uniswap_router.json").read_text())
        self.router: Contract = self.web3.eth.contract(
            address=self.router_address, abi=self.router_abi
        )

    def _to_checksum(self, addr: str) -> str:
        if not isinstance(addr, str) or not Web3.is_address(addr):
            raise ValueError(f"Endereço inválido: {addr}")
        return Web3.to_checksum_address(addr)

    def _has_code(self, addr: str) -> bool:
        code = self.web3.eth.get_code(addr)
        return bool(code and len(code) > 0)

    def _gas_params(self) -> dict:
        base = self.web3.eth.gas_price
        return {
            "maxFeePerGas": int(base * 2),
            "maxPriorityFeePerGas": int(base * 0.1),
        }

    def _nonce(self) -> int:
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _deadline(self) -> int:
        return int(time.time()) + self.tx_deadline

    def _report(self, success: bool, action: str, tx_hex_or_msg: str) -> None:
        prefix = "✅" if success else "❌"
        text = f"{prefix} {action}: {tx_hex_or_msg}"
        send_report(self.bot, text)

    def _build_and_send(
        self,
        tx_dict: dict,
        action: str
    ) -> Optional[str]:
        """
        Assina e envia a transação, reportando resultados via Telegram.
        Retorna tx_hash hex ou None.
        """
        try:
            # estima gas se não fornecido
            if "gas" not in tx_dict:
                estimated = int(self.web3.eth.estimate_gas(tx_dict) * 1.2)
                tx_dict["gas"] = min(estimated, 1_000_000)
        except Exception as e:
            logger.warning(f"Falha ao estimar gas: {e}")
            tx_dict.setdefault("gas", 500_000)

        signed = self.web3.eth.account.sign_transaction(
            tx_dict, private_key=self.private_key
        )
        try:
            raw = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hex = self.web3.to_hex(raw)
            logger.info(f"{action} enviada", extra={"tx": tx_hex})
            self._report(True, action, tx_hex)
            return tx_hex
        except ContractLogicError as cle:
            logger.error(f"{action} revertido", exc_info=True)
            self._report(False, action, str(cle))
        except Exception as e:
            logger.error(f"Erro em {action}", exc_info=True)
            self._report(False, action, str(e))
        return None

    def _amounts_out_minimum(
        self,
        amount_in: int,
        path: List[str],
        slippage_bps: Optional[int]
    ) -> Tuple[int, int]:
        """
        Retorna (amount_out_min, expected) considerando slippage.
        """
        try:
            amounts = self.router.functions.getAmountsOut(amount_in, path).call()
        except Exception as e:
            raise RuntimeError(f"getAmountsOut falhou: {e}")
        expected = amounts[-1]
        bps = slippage_bps or self.default_slippage
        min_out = int(expected * (10_000 - bps) / 10_000)
        if min_out <= 0:
            raise ValueError("amountOutMin calculado <= 0")
        return min_out, expected

    def _contract(self, address: str) -> Contract:
        addr = self._to_checksum(address)
        return self.web3.eth.contract(address=addr, abi=self.erc20_abi)

    def get_token_decimals(self, token_address: str) -> int:
        """
        Retorna decimais de um ERC20, assume 18 em caso de falha.
        """
        try:
            return int(self._contract(token_address).functions.decimals().call())
        except Exception as e:
            logger.warning(f"decimals falhou em {token_address}: {e}")
            return 18

    def approve_token(
        self,
        token_address: str,
        amount: int
    ) -> Optional[str]:
        """
        Aprova `amount` de `token_address` para o router.  
        Retorna hash ou '0xALLOWOK' se já autorizado.
        """
        token = self._contract(token_address)
        allowance = token.functions.allowance(self.wallet, self.router_address).call()
        if allowance >= amount:
            logger.info("Allowance já suficiente", extra={"allowance": allowance})
            return "0xALLOWOK"

        tx = token.functions.approve(self.router_address, amount).build_transaction({
            "from": self.wallet,
            "chainId": self.chain_id,
            "nonce": self._nonce(),
            **self._gas_params(),
        })
        return self._build_and_send(tx, "approve_token")

    def buy_token(
        self,
        token_in_weth: str,
        token_out: str,
        amount_in_wei: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> Optional[str]:
        """
        Executa swap ETH → ERC20.
        amount_in_wei: valor ETH em Wei.
        """
        path = [self._to_checksum(token_in_weth), self._to_checksum(token_out)]
        min_out, _ = self._amounts_out_minimum(amount_in_wei, path, slippage_bps) \
            if amount_out_min in (None, 0) else (amount_out_min, None)

        balance = self.web3.eth.get_balance(self.wallet)
        fee = int(350_000 * self._gas_params()["maxFeePerGas"])
        if amount_in_wei + fee > balance:
            msg = (
                f"ETH insuficiente: disponível={self.web3.from_wei(balance,'ether')} "
                f"necessário≈{self.web3.from_wei(amount_in_wei+fee,'ether')}"
            )
            logger.warning(msg)
            self._report(False, "buy_token", msg)
            return None

        tx = self.router.functions.swapExactETHForTokens(
            min_out, path, self.wallet, self._deadline()
        ).build_transaction({
            "from": self.wallet,
            "value": amount_in_wei,
            "chainId": self.chain_id,
            "nonce": self._nonce(),
            **self._gas_params(),
        })
        return self._build_and_send(tx, "buy_token")

    def sell_token(
        self,
        token_in: str,
        token_out_weth: str,
        amount_in: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> Optional[str]:
        """
        Executa swap ERC20 → ETH.
        amount_in: valor em base units do token.
        """
        path = [self._to_checksum(token_in), self._to_checksum(token_out_weth)]
        min_out, _ = self._amounts_out_minimum(amount_in, path, slippage_bps) \
            if amount_out_min in (None, 0) else (amount_out_min, None)

        # valida saldo do token
        token = self._contract(token_in)
        bal = token.functions.balanceOf(self.wallet).call()
        if amount_in > bal:
            msg = f"Token insuficiente: disponível={bal} necessário={amount_in}"
            logger.warning(msg)
            self._report(False, "sell_token", msg)
            return None

        # garante aprovação
        self.approve_token(token_in, amount_in)

        tx = self.router.functions.swapExactTokensForETH(
            amount_in, min_out, path, self.wallet, self._deadline()
        ).build_transaction({
            "from": self.wallet,
            "chainId": self.chain_id,
            "nonce": self._nonce(),
            **self._gas_params(),
        })
        return self._build_and_send(tx, "sell_token")
