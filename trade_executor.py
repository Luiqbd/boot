import time
import logging
from threading import RLock
from typing import List, Optional
import datetime
from web3 import Web3

from exchange_client import ExchangeClient

logger = logging.getLogger(name)

ERC20DECIMALSABI = [
    {
        "type": "function",
        "name": "decimals",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    }
]


class TradeExecutor:
    """
    Executor básico de ordens de compra e venda.
    """

    def init(
        self,
        w3: Web3,
        wallet_address: str,
        tradesizeeth: float,
        slippage_bps: int,
        dry_run: bool = False,
        dedupettlsec: int = 5
    ):
        self.w3 = w3
        self.walletaddress = walletaddress
        self.tradesize = tradesize_eth
        self.slippagebps = slippagebps
        self.dryrun = dryrun

        # Novo campo: acumulador de PnL simulado
        self.simulated_pnl = 0.0

        self._lock = RLock()
        self.recent = {}       # {(side, tokenin, token_out): timestamp}
        self.ttl = dedupettl_sec

        self.client: Optional[ExchangeClient] = None
        self.openpositionscount = 0  # útil para o /sniper_status

    def setexchangeclient(self, client: ExchangeClient):
        self.client = client

    def _now(self) -> int:
        return int(time.time())

    def key(self, side: str, tokenin: str, token_out: str) -> tuple:
        return (
            side,
            Web3.tochecksumaddress(token_in),
            Web3.tochecksumaddress(token_out),
        )

    def isduplicate(self, side: str, tokenin: str, tokenout: str) -> bool:
        with self._lock:
            key = self.key(side, tokenin, token_out)
            lastts = self.recent.get(key, 0)
            if self.now() - lastts < self._ttl:
                return True
            self.recent[key] = self.now()
            return False

    def decimals(self, tokenaddress: str) -> int:
        erc20 = self.w3.eth.contract(
            address=Web3.tochecksumaddress(token_address),
            abi=ERC20DECIMALSABI
        )
        return int(erc20.functions.decimals().call())

    async def buy(
        self,
        path: List[str],
        amountinwei: int,
        amountoutmin: Optional[int] = None
    ) -> Optional[str]:
        tokenin, tokenout = path[0], path[-1]
        logger.info(f"[BUY] {tokenin} → {tokenout} | ETH={amountinwei} wei minout={amountout_min}")

        if self.isduplicate("buy", tokenin, tokenout):
            logger.warning("[BUY] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            logger.info(f"[DRYRUN] Simulando compra: {tokenin} → {token_out}")
            self.openpositionscount += 1
            return f"SIMULATEDBUY{tokenout}{datetime.datetime.now().isoformat()}"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.buytoken(tokenin, tokenout, amountinwei, amountout_min)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.openpositionscount += 1
            logger.info(f"[BUY] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"[BUY] Falha ao executar compra: {e}", exc_info=True)
            return None

    async def sell(
        self,
        path: List[str],
        amountinwei: int,
        min_out: Optional[int] = None
    ) -> Optional[str]:
        tokenin, tokenout = path[0], path[-1]
        logger.info(f"[SELL] {tokenin} → {tokenout} | amt={amountinwei} minout={minout}")

        if self.isduplicate("sell", tokenin, tokenout):
            logger.warning("[SELL] Ordem duplicada — ignorando")
            return None

        if self.dry_run:
            logger.info(f"[DRYRUN] Simulando venda: {tokenin} → {token_out}")
            self.openpositionscount = max(0, self.openpositionscount - 1)
            return f"SIMULATEDSELL{tokenin}{datetime.datetime.now().isoformat()}"

        if not self.client:
            raise RuntimeError("ExchangeClient não configurado no TradeExecutor")

        try:
            tx = self.client.selltoken(tokenin, tokenout, amountinwei, minout)
            tx_hex = tx.hex() if hasattr(tx, "hex") else str(tx)
            self.openpositionscount = max(0, self.openpositionscount - 1)
            logger.info(f"[SELL] Executada — tx={tx_hex}")
            return tx_hex
        except Exception as e:
            logger.error(f"[SELL] Falha ao executar venda: {e}", exc_info=True)
            return None


RealTradeExecutor = TradeExecutor

class SafeTradeExecutor(TradeExecutor):
    pass
