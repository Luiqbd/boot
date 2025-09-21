# discovery.py

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Dict, List, Optional, Callable

from web3 import Web3
from web3.types import LogReceipt

from config import config
from metrics import (
    PAIRS_DISCOVERED,
    PAIRS_SKIPPED_BASE_FILTER,
    PAIRS_SKIPPED_LOW_LIQ
)
from notifier import send

logger = logging.getLogger(__name__)

# ABI mínima para o evento PoolCreated
FACTORY_ABI = [{
    "anonymous": False,
    "inputs": [
        {"indexed": True,  "internalType": "address", "name": "token0", "type": "address"},
        {"indexed": True,  "internalType": "address", "name": "token1", "type": "address"},
        {"indexed": False, "internalType": "address", "name": "pool",   "type": "address"},
        {"indexed": False, "internalType": "uint24",   "name": "fee",    "type": "uint24"},
    ],
    "name": "PoolCreated",
    "type": "event"
}]

# ABI mínima para getReserves (Uniswap V2)
RESERVES_V2_ABI = [{
    "constant": True,
    "inputs": [],
    "name": "getReserves",
    "outputs": [
        {"internalType": "uint112", "name": "reserve0",           "type": "uint112"},
        {"internalType": "uint112", "name": "reserve1",           "type": "uint112"},
        {"internalType": "uint32",  "name": "blockTimestampLast", "type": "uint32"},
    ],
    "stateMutability": "view",
    "type": "function"
}]

# ABI mínima para decimals (ERC20)
ERC20_DECIMALS_ABI = [{
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
    type: str  # "v2" ou "v3"


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
        self.base_tokens = [t.lower() for t in base_tokens]
        self.min_liq_weth = min_liq_weth
        self.interval = interval_sec
        self.callback = callback

        # último bloco que varremos
        self._last_block = self.web3.eth.block_number

        # tópico PoolCreated já com "0x" na frente
        self._topic = self.web3.to_hex(
            self.web3.keccak(text="PoolCreated(address,address,address,uint24)")
        )

        self._running = False
        self._thread: Optional[threading.Thread] = None

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        if pair.dex.type.lower() != "v2":
            return True
        try:
            pool_ct = self.web3.eth.contract(address=pair.address, abi=RESERVES_V2_ABI)
            r0, r1, _ = pool_ct.functions.getReserves().call()
            if pair.token0.lower() in self.base_tokens:
                amt, token_addr = Decimal(r0), pair.token0
            elif pair.token1.lower() in self.base_tokens:
                amt, token_addr = Decimal(r1), pair.token1
            else:
                return False

            tok_ct = self.web3.eth.contract(address=token_addr, abi=ERC20_DECIMALS_ABI)
            dec = tok_ct.functions.decimals().call()
            normalized = amt / Decimal(10 ** dec)
            logger.debug(
                "Liquidez for token %s: %s (mínima %s)",
                token_addr, normalized, self.min_liq_weth
            )
            return normalized >= self.min_liq_weth
        except Exception as e:
            logger.error("❌ Erro checando liquidez em %s: %s", pair.address, e, exc_info=True)
            return False

    def _parse_log(self, dex: DexInfo, raw: Dict[str, Any]) -> PairInfo:
        event_abi = FACTORY_ABI[0]
        decoded = self.web3.codec.decode_event_log(event_abi, raw["data"], raw["topics"])
        return PairInfo(
            dex=dex,
            address=decoded["pool"],
            token0=decoded["token0"],
            token1=decoded["token1"],
        )

    def _run_loop(self):
        self._running = True
        while self._running:
            current_block = self.web3.eth.block_number
            logger.debug("Scaneando blocos %d → %d", self._last_block + 1, current_block)

            if current_block > self._last_block:
                for dex in self.dexes:
                    try:
                        logs = self.web3.eth.get_logs({
                            "fromBlock": self._last_block + 1,
                            "toBlock":   current_block,
                            "address":   dex.factory,
                            "topics":    [self._topic]
                        })
                    except Exception as e:
                        logger.error("❌ get_logs falhou para %s: %s", dex.name, e)
                        continue

                    for raw in logs:
                        try:
                            pair = self._parse_log(dex, raw)
                        except Exception as e:
                            logger.error("❌ parse_log falhou: %s", e, exc_info=True)
                            continue

                        logger.debug(
                            "PoolCreated em %s → tokens: %s / %s",
                            dex.name, pair.token0, pair.token1
                        )

                        t0, t1 = pair.token0.lower(), pair.token1.lower()
                        if self.base_tokens and not (t0 in self.base_tokens or t1 in self.base_tokens):
                            PAIRS_SKIPPED_BASE_FILTER.inc()
                            logger.debug(
                                "Pulando par %s/%s (nenhum token base)",
                                pair.token0, pair.token1
                            )
                            continue

                        if not asyncio.run(self._has_min_liq(pair)):
                            PAIRS_SKIPPED_LOW_LIQ.inc()
                            continue

                        PAIRS_DISCOVERED.inc()
                        send(
                            f"🔍 Novo par descoberto:\n"
                            f"• DEX: {dex.name}\n"
                            f"• Pool: {pair.address}\n"
                            f"• Tokens: {pair.token0} / {pair.token1}"
                        )
                        asyncio.create_task(self.callback(
                            pair.address, pair.token0, pair.token1, dex
                        ))

                self._last_block = current_block

            time.sleep(self.interval)

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("⚠️ SniperDiscovery já está rodando")
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("▶️ SniperDiscovery iniciado")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("🔴 SniperDiscovery parado")


# módulo-level control
_discovery: Optional[SniperDiscovery] = None

def subscribe_new_pairs(callback: Callable[..., Awaitable[Any]]):
    global _discovery
    if _discovery and _discovery._running:
        logger.warning("⚠️ Discovery já ativo")
        return

    dexes = [DexInfo(d.name, d.factory, d.router, d.type) for d in config["DEXES"]]
    base_tokens = config["BASE_TOKENS"]
    min_liq = config["MIN_LIQ_WETH"]
    interval = config["DISCOVERY_INTERVAL"]

    _discovery = SniperDiscovery(
        web3=Web3(Web3.HTTPProvider(config["RPC_URL"])),
        dexes=dexes,
        base_tokens=base_tokens,
        min_liq_weth=Decimal(str(min_liq)),
        interval_sec=interval,
        callback=callback
    )
    _discovery.start()

def stop_discovery():
    if _discovery:
        _discovery.stop()

def is_discovery_running() -> bool:
    return bool(_discovery and _discovery._running)
