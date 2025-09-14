discovery.py

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional

from web3 import Web3
from web3.contract import Contract
from web3.types import LogReceipt

from config import config
from metrics import PAIRS_DISCOVERED
from notifier import send

logger = logging.getLogger(name)

Minimal ABIs for decoding events and reserves
FACTORYABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "token0", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "token1", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "pool", "type": "address"},
            {"indexed": False, "internalType": "uint24",   "name": "fee",  "type": "uint24"}
        ],
        "name": "PoolCreated",
        "type": "event"
    }
]

PAIRV2_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "reserve1", "type": "uint112"},
            {"internalType": "uint32",  "name": "blockTimestampLast", "type": "uint32"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

ERC20ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]

@dataclass(frozen=True)
class DexInfo:
    name: str
    factory: str
    router: str
    type: str

@dataclass
class PairInfo:
    dex: DexInfo
    address: str
    token0: str
    token1: str

class SniperDiscovery:
    def init(
        self,
        web3: Web3,
        dexes: List[DexInfo],
        base_tokens: List[str],
        minliqweth: Decimal,
        interval_sec: int,
        callback: Callable[[str, str, str, DexInfo], Awaitable[Any]],
    ):
        self.web3 = web3
        self.dexes = dexes
        self.basetokens = [t.lower() for t in basetokens]
        self.minliqweth = minliqweth
        self.interval = interval_sec
        self.callback = callback

        self._running = False
        self._thread: Optional[threading.Thread] = None

        # prepare filters for each dex factory
        self._filters: List[Dict[str, Any]] = []
        for dex in self.dexes:
            factorycontract = web3.eth.contract(address=dex.factory, abi=FACTORY_ABI)
            evt = factory_contract.events.PoolCreated
            f = evt.createFilter(fromBlock="latest")
            self._filters.append({
                "dex": dex,
                "filter": f
            })
            logger.info("🟢 Subscribed to PoolCreated on %s (factory %s)", dex.name, dex.factory)

    def parselog(self, dex: DexInfo, log_tx: LogReceipt) -> Optional[PairInfo]:
        try:
            args = log_tx["args"]
            return PairInfo(
                dex=dex,
                address=args["pool"],
                token0=args["token0"],
                token1=args["token1"]
            )
        except Exception as e:
            logger.error("❌ Falha ao parsear log em %s: %s", dex.name, e, exc_info=True)
            return None

    async def hasmin_liq(self, pair: PairInfo) -> bool:
        # Only V2 pools have getReserves. For V3 skip liquidity check.
        if pair.dex.type.lower() != "v2":
            return True

        try:
            pool = self.web3.eth.contract(address=pair.address, abi=PAIRV2_ABI)
            reserve0, reserve1, _ = pool.functions.getReserves().call()

            # Determine which reserve corresponds to a base token
            if pair.token0.lower() in self.base_tokens:
                amount = Decimal(reserve0)
                token_addr = pair.token0
            elif pair.token1.lower() in self.base_tokens:
                amount = Decimal(reserve1)
                token_addr = pair.token1
            else:
                # no base token in this pair
                return False

            # get decimals of that token
            tokencontract = self.web3.eth.contract(address=tokenaddr, abi=ERC20ABI)
            d = token_contract.functions.decimals().call()
            normalized = amount / Decimal(10  d)

            return normalized >= self.minliqweth
        except Exception as e:
            logger.error("❌ Erro checando liquidez em %s: %s", pair.address, e, exc_info=True)
            return False

    def runloop(self):
        loop = asyncio.neweventloop()
        asyncio.seteventloop(loop)
        self._running = True

        while self._running:
            for entry in self._filters:
                dex = entry["dex"]
                filt = entry["filter"]
                try:
                    logs = filt.getnewentries()
                except Exception as e:
                    logger.error("❌ Falha ao buscar logs para %s: %s", dex.name, e)
                    continue

                for log in logs:
                    pair = self.parselog(dex, log)
                    if not pair:
                        continue

                    # filter by base tokens
                    t0 = pair.token0.lower()
                    t1 = pair.token1.lower()
                    if self.basetokens and not (t0 in self.basetokens or t1 in self.base_tokens):
                        continue

                    # filter by minimum liquidity
                    if not loop.rununtilcomplete(self.hasmin_liq(pair)):
                        continue

                    # metric + notify + callback
                    PAIRS_DISCOVERED.inc()
                    send(
                        f"🔍 Novo par descoberto:\n"
                        f"• DEX: {dex.name}\n"
                        f"• Par: {pair.address}\n"
                        f"• Tokens: {pair.token0} / {pair.token1}"
                    )
                    # schedule the user callback on the same loop
                    loop.create_task(self.callback(
                        pair.address,
                        pair.token0,
                        pair.token1,
                        dex
                    ))

            time.sleep(self.interval)

    def start(self):
        if self.thread and self.thread.is_alive():
            logger.warning("⚠️ SniperDiscovery já está rodando")
            return
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self._thread.start()
        logger.info("▶️ SniperDiscovery thread iniciada")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("🔴 SniperDiscovery parado")

module‐level control
_discovery: Optional[SniperDiscovery] = None

def subscribenewpairs(callback: Callable[..., Awaitable[Any]]):
    global _discovery
    if discovery and discovery._running:
        logger.warning("⚠️ Discovery já ativo, ignorando nova subscription")
        return

    # parse config
    dexes = [
        DexInfo(
            name=d["name"],
            factory=d["factory"],
            router=d["router"],
            type=d["type"]
        )
        for d in config["DEXES"]
    ]
    basetokens = config.get("BASETOKENS", [])
    minliq = Decimal(str(config.get("MINLIQ_WETH",
