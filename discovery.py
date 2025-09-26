import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Dict, List, Optional, Callable

try:
    from web3 import Web3
    from web3.types import LogReceipt
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
    LogReceipt = None
from config import config
from metrics import (
    PAIRS_DISCOVERED,
    PAIRS_SKIPPED_NO_BASE,
    PAIRS_SKIPPED_LOW_LIQ
)
from notifier import send
import asyncio

logger = logging.getLogger(__name__)

FACTORY_ABI = [{ ... }]  # mesmo ABI
RESERVES_V2_ABI = [{ ... }]
ERC20_DECIMALS_ABI = [{ ... }]

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
        loop: asyncio.AbstractEventLoop
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = [t.lower() for t in base_tokens]
        self.min_liq_weth = min_liq_weth
        self.interval = interval_sec
        self.callback = callback
        self.loop = loop
        self._last_block = self.web3.eth.block_number
        self._topic = self.web3.to_hex(
            self.web3.keccak(text="PoolCreated(address,address,address,uint24)")
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def _schedule(self, coro):
        self.loop.call_soon_threadsafe(asyncio.create_task, coro)

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        if pair.dex.type.lower() != "v2":
            return True
        try:
            pool = self.web3.eth.contract(pair.address, RESERVES_V2_ABI)
            r0, r1, _ = pool.functions.getReserves().call()
            if pair.token0.lower() in self.base_tokens:
                amt, tok = Decimal(r0), pair.token0
            elif pair.token1.lower() in self.base_tokens:
                amt, tok = Decimal(r1), pair.token1
            else:
                return False
            dec = self.web3.eth.contract(tok, ERC20_DECIMALS_ABI).functions.decimals().call()
            norm = amt / Decimal(10**dec)
            logger.debug("Liquidez %s: %s >= %s ?",
                         tok, norm, self.min_liq_weth)
            return norm >= self.min_liq_weth
        except Exception as e:
            logger.error("Erro check_liq %s: %s", pair.address, e, exc_info=True)
            return False

    def _parse_log(self, dex: DexInfo, raw: Dict[str, Any]) -> PairInfo:
        decoded = self.web3.codec.decode_event_log(FACTORY_ABI[0], raw["data"], raw["topics"])
        return PairInfo(dex, decoded["pool"], decoded["token0"], decoded["token1"])

    def _run(self):
        self._running = True
        while self._running:
            curr = self.web3.eth.block_number
            if curr <= self._last_block:
                time.sleep(self.interval)
                continue

            for dex in self.dexes:
                try:
                    logs = self.web3.eth.get_logs({
                        "fromBlock": self._last_block + 1,
                        "toBlock": curr,
                        "address": dex.factory,
                        "topics": [self._topic]
                    })
                except Exception as e:
                    logger.error("get_logs %s: %s", dex.name, e)
                    continue

                for raw in logs:
                    pair = self._parse_log(dex, raw)
                    t0, t1 = pair.token0.lower(), pair.token1.lower()
                    if self.base_tokens and not (t0 in self.base_tokens or t1 in self.base_tokens):
                        PAIRS_SKIPPED_NO_BASE.inc()
                        continue
                    has_liq = asyncio.run(self._has_min_liq(pair))
                    if not has_liq:
                        PAIRS_SKIPPED_LOW_LIQ.inc()
                        continue
                    PAIRS_DISCOVERED.inc()
                    send(f"🔍 Par: {pair.address} tokens {pair.token0}/{pair.token1}")
                    self._schedule(self.callback(pair.address, pair.token0, pair.token1, dex))

            self._last_block = curr
            time.sleep(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(1)

_discovery: Optional[SniperDiscovery] = None

def subscribe_new_pairs(callback, loop=None):
    global _discovery
    if loop is None:
        loop = asyncio.get_event_loop()
    if _discovery and _discovery._running:
        return
    dexes = [DexInfo(d.name, d.factory, d.router, d.type) for d in config["DEXES"]]
    _discovery = SniperDiscovery(
        Web3(Web3.HTTPProvider(config["RPC_URL"])),
        dexes,
        config["BASE_TOKENS"],
        config["MIN_LIQ_WETH"],
        config["DISCOVERY_INTERVAL"],
        callback,
        loop
    )
    _discovery.start()

def stop_discovery():
    if _discovery:
        _discovery.stop()

def is_discovery_running() -> bool:
    return bool(_discovery and _discovery._running)
