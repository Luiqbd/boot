import time
import logging
from threading import RLock
from typing import List, Optional

from decimal import Decimal, InvalidOperation
from web3 import Web3

from exchange_client import ExchangeClient

logger = logging.getLogger(__name__)

# ABI mínima para consultar decimals de um token ERC20
ERC20_DECIMALS_ABI = [{
    "type": "function",
    "name": "decimals",
    "stateMutability": "view",
    "inputs": [],
    "outputs": [{"name": "", "type": "uint8"}],
}]


class TradeExecutor:
    """
    Executor básico de ordens de compra e venda.
    
    Construtor:
      TradeExecutor(
          w3,
          wallet_address,
          trade_size_eth,
          slippage_bps,
          dry_run=False
      )
    
    Após instanciar, chame:
      executor.set_exchange_client(
          ExchangeClient(w3, router_contract)
      )
    """

    def __init__(
        self,
        w3: Web3,
        wallet_address: str,
        trade_size_eth: float,
        slippage_bps: int,
        dry_run: bool = False,
        dedupe_ttl_sec: int = 5
    ):
        self.w3 = w3
        self.wallet_address = wallet_address
        self.trade_size = trade_size_eth
        self.slippage_bps = slippage_bps
        self.dry_run = dry_run

        self._lock = RLock()
        self._recent = {}       # {(side, token_in, token_out): timestamp}
        self._ttl = dedupe_ttl_sec

        self.client: Optional[ExchangeClient] = None

    def set_exchange_client(self, client: ExchangeClient):
        """
        Define o ExchangeClient que será usado para enviar transações.
        Deve ser chamado antes de buy() ou sell().
        """
        self.client = client

    def _now(self) -> int:
        return int(time.time())

    def _key(self, side: str, token_in: str, token_out: str) -> tuple:
        return (
            side,
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
        )

    def _is_duplicate(self, side: str, token_in: str, token_out: str) -> bool:
        """
        Verifica se uma ordem idêntica foi executada recentemente
        para evitar duplicação em curto prazo.
        """
        with self._lock:
            key = self._key(side, token_in, token_out)
            last_ts = self._recent.get(key, 0)
            if self._now() - last_ts < self._ttl:
                return True
            self._recent[key] = self._now()
            return False

    def _decimals(self, token_address: str) -> int:
        """
        Obtém o número de decimais de um token via ABI mínima.
        """
        erc20 = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=ERC20_DECIMALS_ABI
        )
        return int(erc20.functions.decimals().call())

    async def buy(
        self,
        path: List[str],
        amount_in_wei: int,
        amount_out_min: Optional[int] = None
    ) -> Optional[str]:
        """
        Realiza a compra de ETH por token.
        
        path: [weth_address, token_address]
        amount_in_wei: quantidade de ETH em wei
        amount_out_min: mínimo de tokens a receber
        
        Retorna o hash da transação em hex ou None.
        """
        token_in, token_out = path[0], path[-1]
        logger.info(f"[BUY] {token_in} → {token_out} | ETH={amount_in_wei} wei min_out={amount_out_min}")

        if self._is_duplicate("buy", token_in, token_out):
            logger.warning("[BUY] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            logger.info(f"[DRY_RUN] Simulando compra: {token_in} → {token_out}")
            return "0xDRYRUN"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.buy_token(token_in, token_out, amount_in_wei, amount_out_min)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            logger.info(f"[BUY] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"[BUY] Falha ao executar compra: {e}", exc_info=True)
            return None

    async def sell(
        self,
        path: List[str],
        amount_in_wei: int,
        min_out: Optional[int] = None
    ) -> Optional[str]:
        """
        Realiza a venda de token por ETH.
        
        path: [token_address, weth_address]
        amount_in_wei: quantidade de token (base units)
        min_out: mínimo de ETH em wei a receber
        
        Retorna o hash da transação em hex ou None.
        """
        token_in, token_out = path[0], path[-1]
        logger.info(f"[SELL] {token_in} → {token_out} | amt={amount_in_wei} min_out={min_out}")

        if self._is_duplicate("sell", token_in, token_out):
            logger.warning("[SELL] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            logger.info(f"[DRY_RUN] Simulando venda: {token_in} → {token_out}")
            return "0xDRYRUN"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.sell_token(token_in, token_out, amount_in_wei, min_out)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            logger.info(f"[SELL] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"[SELL] Falha ao executar venda: {e}", exc_info=True)
            return None


# Alias para manter compatibilidade com import em main.py
RealTradeExecutor = TradeExecutor
