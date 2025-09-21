import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional

from web3 import Web3
from web3.types import LogReceipt

from config import config
from metrics import PAIRS_DISCOVERED
from notifier import send

logger = logging.getLogger(__name__)

# Minimal ABI for PoolCreated event
FACTORY_ABI = [{
    "anonymous": False,
    "inputs": [
        {"indexed": True, "internalType": "address", "name": "token0", "type": "address"},
        {"indexed": True, "internalType": "address", "name": "token1", "type": "address"},
        {"indexed": False, "internalType": "address", "name": "pool",  "type": "address"},
        {"indexed": False, "internalType": "uint24",  "name": "fee",   "type": "uint24"}
    ],
    "name": "PoolCreated",
    "type": "event"
}]

PAIR_V2_ABI = [{
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
}]

ERC20_ABI = [{
    "constant": True,
    "inputs": [],
    "name": "decimals",
    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
    "stateMutability": "view",
    "type": "function"
}]


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
    def __init__(
        self,
        web3: Web3,
        dexes: List[DexInfo],
        base_tokens: List[str],
        min_liq_weth: Decimal,
        interval_sec: int,
        callback: Callable[[str, str, str, DexInfo], Awaitable[Any]],
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = base_tokens
        self.min_liq_weth = min_liq_weth
        self.interval = interval_sec
        self.callback = callback
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._filters: List[Dict[str, Any]] = []

        for dex in self.dexes:
            factory = self.web3.eth.contract(address=dex.factory, abi=FACTORY_ABI)
            event   = factory.events.PoolCreated
            filt    = event.createFilter(fromBlock="latest")
            self._filters.append({"dex": dex, "filter": filt})
            logger.info("🟢 Subscribed to PoolCreated on %s (factory %s)", dex.name, dex.factory)

    def _parse_log(self, dex: DexInfo, log_tx: LogReceipt) -> Optional[PairInfo]:
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

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        if pair.dex.type.lower() != "v2":
            return True
        try:
            pool = self.web3.eth.contract(address=pair.address, abi=PAIR_V2_ABI)
            r0, r1, _ = pool.functions.getReserves().call()
            if pair.token0.lower() in self.base_tokens:
                amt, token = Decimal(r0), pair.token0
            elif pair.token1.lower() in self.base_tokens:
                amt, token = Decimal(r1), pair.token1
            else:
                return False
            erc = self.web3.eth.contract(address=token, abi=ERC20_ABI)
            dec = erc.functions.decimals().call()
            norm = amt / Decimal(10 ** dec)
            return norm >= self.min_liq_weth
        except Exception as e:
            logger.error("❌ Erro checando liquidez em %s: %s", pair.address, e, exc_info=True)
            return False

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._running = True

        while self._running:
            for entry in self._filters:
                dex  = entry["dex"]
                filt = entry["filter"]
                try:
                    logs = filt.get_new_entries()
                except Exception as e:
                    logger.error("❌ Falha ao buscar logs para %s: %s", dex.name, e)
                    continue

                for log in logs:
                    pair = self._parse_log(dex, log)
                    if not pair:
                        continue

                    t0, t1 = pair.token0.lower(), pair.token1.lower()
                    if self.base_tokens and not (t0 in self.base_tokens or t1 in self.base_tokens):
                        continue

                    if not loop.run_until_complete(self._has_min_liq(pair)):
                        continue

                    PAIRS_DISCOVERED.inc()
                    send(
                        f"🔍 Novo par descoberto:\n"
                        f"• DEX: {dex.name}\n"
                        f"• Par: {pair.address}\n"
                        f"• Tokens: {pair.token0} / {pair.token1}"
                    )
                    loop.create_task(self.callback(
                        pair.address, pair.token0, pair.token1, dex
                    ))

            time.sleep(int(config["DISCOVERY_INTERVAL"]))

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("⚠️ SniperDiscovery já está rodando")
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("▶️ SniperDiscovery thread iniciada")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("🔴 SniperDiscovery parado")


# module-level control
_discovery: Optional[SniperDiscovery] = None

def subscribe_new_pairs(callback: Callable[..., Awaitable[Any]]):
    global _discovery
    if _discovery and _discovery._running:
        logger.warning("⚠️ Discovery já ativo")
        return

    dexes_raw = config["DEXES"]
    dexes = [DexInfo(d.name, d.factory, d.router, d.type) for d in dexes_raw]
    base  = config["BASE_TOKENS"]
    min_l = config["MIN_LIQ_WETH"]
    interval = config["DISCOVERY_INTERVAL"]

    _discovery = SniperDiscovery(
        web3=Web3(Web3.HTTPProvider(config["RPC_URL"])),
        dexes=dexes,
        base_tokens=base,
        min_liq_weth=Decimal(str(min_l)),
        interval_sec=interval,
        callback=callback
    )
    _discovery.start()

def stop_discovery():
    if _discovery:
        _discovery.stop()

def is_discovery_running() -> bool:
    return bool(_discovery and _discovery._running)
