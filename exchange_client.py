import json
import logging
from typing import Tuple, List, Optional
from decimal import Decimal

from web3 import Web3
from eth_account import Account
from web3.exceptions import ContractLogicError

from config import config
from telegram import Bot
from telegram_alert import send_report

logger = logging.getLogger(__name__)

# Bot de notificações centralizado
bot_notify = Bot(token=config["TELEGRAM_TOKEN"])


def _to_wei_eth(web3: Web3, amount_eth) -> int:
    """Converte valor em ETH para Wei com Decimal para precisão."""
    return web3.to_wei(Decimal(str(amount_eth)), "ether")


def _is_empty_code(code) -> bool:
    """Retorna True se o contrato não estiver implantado (bytecode vazio)."""
    return code is None or len(code) == 0


class ExchangeClient:
    def __init__(self, router_address: str):
        """
        :param router_address: Endereço do router (ex.: Uniswap/PancakeSwap) detectado dinamicamente
        """
        self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
        self.private_key = config["PRIVATE_KEY"]
        self.wallet = Account.from_key(self.private_key).address

        env_wallet = (config.get("WALLET_ADDRESS") or "").strip()
        if env_wallet and Web3.to_checksum_address(env_wallet) != Web3.to_checksum_address(self.wallet):
            raise ValueError("WALLET_ADDRESS difere da PRIVATE_KEY")

        # Valida router on-chain
        self.router_address = Web3.to_checksum_address(router_address)
        code = self.web3.eth.get_code(self.router_address)
        if _is_empty_code(code):
            raise ValueError(f"Router {self.router_address} não implantado na rede")

        # Abis
        with open("abis/uniswap_router.json") as f:
            self.router_abi = json.load(f)
        with open("abis/erc20.json") as f:
            self.erc20_abi = json.load(f)

        self.router = self.web3.eth.contract(address=self.router_address, abi=self.router_abi)

    def _gas_params(self) -> dict:
        base_fee = int(self.web3.eth.gas_price)
        return {
            "maxFeePerGas": int(base_fee * 2),
            "maxPriorityFeePerGas": int(base_fee * 0.1),
        }

    def _nonce(self) -> int:
        return self.web3.eth.get_transaction_count(self.wallet, "pending")

    def _amount_out_min(
        self, amount_in: int, path: List[str], slippage_bps: Optional[int] = None
    ) -> Tuple[int, int]:
        try:
            amounts = self.router.functions.getAmountsOut(int(amount_in), path).call()
        except Exception as e:
            raise RuntimeError(f"Falha ao consultar getAmountsOut: {e}")
        expected = int(amounts[-1])
        bps = slippage_bps or int(config["DEFAULT_SLIPPAGE_BPS"])
        min_out = int(expected * (1 - bps / 10_000))
        if min_out <= 0:
            raise ValueError("amountOutMin calculado <= 0")
        return min_out, expected

    def get_token_decimals(self, token_address: str) -> int:
        token = self.web3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=self.erc20_abi
        )
        try:
            return int(token.functions.decimals().call())
        except Exception as e:
            logger.warning(f"Falha ao obter decimals de {token_address}: {e}; assumindo 18")
            return 18

    def approve_token(self, token_address: str, amount_base_units: int) -> Optional[str]:
        token_address = Web3.to_checksum_address(token_address)
        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] approve_token skip")
            return "0xDRYRUN"

        token = self.web3.eth.contract(address=token_address, abi=self.erc20_abi)
        allowance = int(token.functions.allowance(self.wallet, self.router_address).call())
        if allowance >= amount_base_units:
            return "0xALLOWOK"

        tx = token.functions.approve(self.router_address, amount_base_units).build_transaction({
            "from": self.wallet,
            "chainId": int(config["CHAIN_ID"]),
            **self._gas_params(),
            "nonce": self._nonce(),
        })
        try:
            tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        except Exception as e:
            logger.warning(f"Falha ao estimar gas para approve: {e}; usando 120k")
            tx["gas"] = 120_000

        try:
            signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
            raw = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hex = self.web3.to_hex(raw)
            send_report(bot_notify, f"✅ approve_token: {tx_hex}")
            return tx_hex

        except Exception as e:
            msg = f"❌ Erro em approve_token: {e}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            return None

    def buy_token(
        self,
        token_in_weth: str,
        token_out: str,
        amount_in_wei: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> Optional[str]:
        path = [Web3.to_checksum_address(token_in_weth), Web3.to_checksum_address(token_out)]
        if amount_out_min in (None, 0):
            amount_out_min, _ = self._amount_out_min(amount_in_wei, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] buy_token skip")
            return "0xDRYRUN"

        # cheque rápido de saldo ETH
        balance = self.web3.eth.get_balance(self.wallet)
        # estimativa de custo: value + gas*maxFeePerGas
        gas_est = int(350_000)
        fee = gas_est * self._gas_params()["maxFeePerGas"]
        if amount_in_wei + fee > balance:
            msg = (
                f"⚠️ Saldo ETH insuficiente para buy_token:\n"
                f"  disponível = {self.web3.from_wei(balance, 'ether')} ETH\n"
                f"  necessário ≈ {self.web3.from_wei(amount_in_wei + fee, 'ether')} ETH"
            )
            logger.warning(msg)
            send_report(bot_notify, msg)
            return None

        deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + int(config.get("TX_DEADLINE_SEC", 300))
        tx = self.router.functions.swapExactETHForTokens(
            amount_out_min, path, self.wallet, deadline
        ).build_transaction({
            "from": self.wallet,
            "value": amount_in_wei,
            "chainId": int(config["CHAIN_ID"]),
            **self._gas_params(),
            "nonce": self._nonce(),
        })

        try:
            tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        except Exception:
            tx["gas"] = 350_000

        try:
            signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
            raw = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hex = self.web3.to_hex(raw)
            send_report(bot_notify, f"✅ buy_token: {tx_hex}")
            return tx_hex

        except ContractLogicError as cle:
            msg = f"❌ swapExactETHForTokens revertido: {cle}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            return None

        except Exception as e:
            msg = f"❌ Erro em buy_token: {e}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            return None

    def sell_token(
        self,
        token_in: str,
        token_out_weth: str,
        amount_in_base_units: int,
        amount_out_min: Optional[int] = None,
        slippage_bps: Optional[int] = None
    ) -> Optional[str]:
        path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out_weth)]
        if amount_out_min in (None, 0):
            amount_out_min, _ = self._amount_out_min(amount_in_base_units, path, slippage_bps)

        if config.get("DRY_RUN"):
            logger.info("[DRY_RUN] sell_token skip")
            return "0xDRYRUN"

        # conferir saldo do token
        token = self.web3.eth.contract(address=path[0], abi=self.erc20_abi)
        bal_token = token.functions.balanceOf(self.wallet).call()
        if amount_in_base_units > bal_token:
            msg = (
                f"⚠️ Saldo de token insuficiente:\n"
                f"  disponível = {bal_token / (10 ** self.get_token_decimals(token_in))}\n"
                f"  necessário = {amount_in_base_units / (10 ** self.get_token_decimals(token_in))}"
            )
            logger.warning(msg)
            send_report(bot_notify, msg)
            return None

        self.approve_token(token_in, amount_in_base_units)

        deadline = int(self.web3.eth.get_block("latest")["timestamp"]) + int(config.get("TX_DEADLINE_SEC", 300))
        tx = self.router.functions.swapExactTokensForETH(
            amount_in_base_units, amount_out_min, path, self.wallet, deadline
        ).build_transaction({
            "from": self.wallet,
            "chainId": int(config["CHAIN_ID"]),
            **self._gas_params(),
            "nonce": self._nonce(),
        })

        try:
            tx["gas"] = int(self.web3.eth.estimate_gas(tx) * 1.2)
        except Exception:
            tx["gas"] = 400_000

        try:
            signed = self.web3.eth.account.sign_transaction(tx, self.private_key)
            raw = self.web3.eth.send_raw_transaction(signed.rawTransaction)
            tx_hex = self.web3.to_hex(raw)
            send_report(bot_notify, f"✅ sell_token: {tx_hex}")
            return tx_hex

        except ContractLogicError as cle:
            msg = f"❌ swapExactTokensForETH revertido: {cle}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            return None

        except Exception as e:
            msg = f"❌ Erro em sell_token: {e}"
            logger.error(msg, exc_info=True)
            send_report(bot_notify, msg)
            return None
