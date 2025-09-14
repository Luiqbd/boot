# discovery.py

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

logger = logging.getLogger(__name__)

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
    """
    Descobre novos pares em várias DEXes e dispara callback.
    """

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
        self.base_tokens = [Web3.to_checksum_address(t) for t in base_tokens]
        self.min_liq_wei = int(min_liq_weth * Decimal(10**18))
        self.interval = interval_sec
        self.callback = callback

        self._stop = threading.Event()
        self._last_block: Dict[str, int] = {}
        self._start_ts = 0.0

        self.SIG_V2 = Web3.to_hex(
            Web3.keccak(text="PairCreated(address,address,address,uint256)")
        )
        self.SIG_V3 = Web3.to_hex(
            Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)")
        )

        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _decode_addr(self, topic: bytes) -> str:
        return Web3.to_checksum_address("0x" + topic.hex()[-40:])

    def _init_blocks(self) -> None:
        bloco_atual = self.web3.eth.block_number
        for dex in self.dexes:
            self._last_block[dex.name] = bloco_atual

    def start(self) -> None:
        if self._start_ts:
            logger.warning("SniperDiscovery já está rodando")
            return

        self._start_ts = time.time()
        self._stop.clear()
        self._init_blocks()

        self._loop = asyncio.new_event_loop()

        def _run():
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._poll_loop())
            self._loop.run_forever()

        threading.Thread(target=_run, daemon=True).start()
        logger.info("Descoberta de pares iniciada")

    def stop(self) -> None:
        self._stop.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("Descoberta de pares parada")

    def is_running(self) -> bool:
        return not self._stop.is_set()

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                bloco_atual = self.web3.eth.block_number
                for dex in self.dexes:
                    inicio = self._last_block[dex.name] + 1
                    if bloco_atual < inicio:
                        continue

                    sig = self.SIG_V2 if dex.type == "v2" else self.SIG_V3
                    logs: List[LogReceipt] = self.web3.eth.get_logs({
                        "fromBlock": inicio,
                        "toBlock": bloco_atual,
                        "address": dex.factory,
                        "topics": [sig],
                    })
                    self._last_block[dex.name] = bloco_atual

                    for log_tx in logs:
                        pair = self._parse_log(dex, log_tx)
                        if not pair:
                            continue

                        if not {pair.token0, pair.token1} & set(self.base_tokens):
                            continue

                        # métrica de par descoberto
                        PAIRS_DISCOVERED.inc()

                        if not await self._has_min_liq(pair):
                            continue

                        try:
                            coro = self.callback(
                                pair.address,
                                pair.token0,
                                pair.token1,
                                dex
                            )
                            if asyncio.iscoroutine(coro):
                                await coro
                        except Exception as e:
                            logger.error("Erro no callback de discovery: %s", e, exc_info=True)

            except Exception as e:
                logger.error("Erro no loop de discovery: %s", e, exc_info=True)

            await asyncio.sleep(self.interval)

    def _parse_log(self, dex: DexInfo, log_tx: LogReceipt) -> Optional[PairInfo]:
        try:
            t0 = self._decode_addr(log_tx["topics"][1])
            t1 = self._decode_addr(log_tx["topics"][2])
            raw = log_tx["data"].hex() if hasattr(log_tx["data"], "hex") else log_tx["data"]
            data = raw[2:] if raw.startswith("0x") else raw
            word = data[0:64] if dex.type == "v2" else data[-64:]
            addr = self._decode_addr(bytes.fromhex(word))
            return PairInfo(dex=dex, address=addr, token0=t0, token1=t1)
        except Exception as e:
            logger.warning("Falha ao parsear log %s: %s", dex.name, e)
            return None

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        if pair.dex.type != "v2":
            return True
        try:
            abi = [
                {"inputs": [], "name": "getReserves", "outputs": [
                    {"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}],
                 "stateMutability": "view", "type": "function"},
                {"inputs": [], "name": "token0", "outputs": [{"type": "address"}], "type": "function"},
            ]
            contrato = self.web3.eth.contract(address=pair.address, abi=abi)
            r0, r1, _ = contrato.functions.getReserves().call()
            t0 = contrato.functions.token0().call().lower()
            reserva = r0 if t0 == self.base_tokens[0].lower() else r1
            return reserva >= self.min_liq_wei
        except Exception:
            return False

# API de controle
_discovery: Optional[SniperDiscovery] = None

def subscribe_new_pairs(
    callback: Callable[..., Awaitable[Any]]
) -> None:
    global _discovery
    if _discovery and _discovery.is_running():
        logger.warning("Discovery já iniciado")
        return

    dexes_cfg = config["DEXES"]
    dexes = [DexInfo(**d) for d in dexes_cfg]
    base_tokens = config.get("BASE_TOKENS", [])
    min_liq = Decimal(str(config["MIN_LIQ_WETH"]))
    interval = int(config["DISCOVERY_INTERVAL"])

    web3_client = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    _discovery = SniperDiscovery(
        web3=web3_client,
        dexes=dexes,
        base_tokens=base_tokens,
        min_liq_weth=min_liq,
        interval_sec=interval,
        callback=callback
    )
    _discovery.start()

def stop_discovery() -> None:
    if _discovery:
        _discovery.stop()

def is_discovery_running() -> bool:
    return bool(_discovery and _discovery.is_running())
