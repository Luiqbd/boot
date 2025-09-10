# discovery.py

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from web3 import Web3
from web3.types import LogReceipt

from config import config
from utils import escape_md_v2, _notify

logger = logging.getLogger("discovery")


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
        callback: Callable[[PairInfo], Awaitable[Any]],
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = [Web3.to_checksum_address(t) for t in base_tokens]
        self.min_liq_wei = int(min_liq_weth * Decimal(10**18))
        self.interval = interval_sec
        self.callback = callback

        self._stop = threading.Event()
        self._last_block: Dict[str, int] = {}
        self._start_ts: float = 0.0

        self.pair_count = 0
        self.last_pair: Optional[PairInfo] = None

        # Sinais de evento PairCreated / PoolCreated
        self.SIG_V2 = Web3.to_hex(Web3.keccak(text="PairCreated(address,address,address,uint256)"))
        self.SIG_V3 = Web3.to_hex(Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)"))

        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _decode_addr(self, topic: bytes) -> str:
        """Extrai e retorna endereço checksum de um tópico."""
        return Web3.to_checksum_address("0x" + topic.hex()[-40:])

    def _init_blocks(self) -> None:
        """Inicializa último bloco processado para cada DEX."""
        blk = self.web3.eth.block_number
        for dex in self.dexes:
            self._last_block[dex.name] = blk

    def start(self) -> None:
        """
        Inicia o polling em background para descobrir novos pares.
        """
        if self._start_ts:
            logger.warning("SniperDiscovery já está rodando")
            return

        self._start_ts = time.time()
        self._stop.clear()
        self._init_blocks()

        self._loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._poll_loop())
            self._loop.run_forever()

        threading.Thread(target=_run, daemon=True).start()

        _notify("🔍 SniperDiscovery iniciado", via_alert=True)
        logger.info("SniperDiscovery iniciado")

    def stop(self) -> None:
        """
        Solicita parada do polling de discovery.
        """
        self._stop.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        _notify("🛑 SniperDiscovery parado", via_alert=True)
        logger.info("SniperDiscovery parado")

    def is_running(self) -> bool:
        """Retorna True se o discovery estiver ativo."""
        return not self._stop.is_set()

    async def _poll_loop(self) -> None:
        """
        Loop contínuo que busca logs de criação de pares e dispara o callback.
        """
        while not self._stop.is_set():
            try:
                latest = self.web3.eth.block_number
                for dex in self.dexes:
                    start_blk = self._last_block[dex.name] + 1
                    if latest < start_blk:
                        continue

                    sig = self.SIG_V2 if dex.type == "v2" else self.SIG_V3
                    logs: List[LogReceipt] = self.web3.eth.get_logs({
                        "fromBlock": start_blk,
                        "toBlock": latest,
                        "address": dex.factory,
                        "topics": [sig],
                    })
                    self._last_block[dex.name] = latest

                    for entry in logs:
                        pair = self._parse_log(dex, entry)
                        if not pair:
                            continue

                        # Filtra base_tokens
                        if not {pair.token0, pair.token1} & set(self.base_tokens):
                            continue

                        # Notifica no Telegram
                        msg = (
                            f"🆕 [{pair.dex.name}] Novo par\n"
                            f"{pair.address}\n"
                            f"{pair.token0[:6]}… / {pair.token1[:6]}…"
                        )
                        _notify(msg, via_alert=True)

                        # Verifica liquidez on-chain
                        if not await self._has_min_liq(pair):
                            _notify(f"⏳ Sem liquidez mínima: {pair.address}", via_alert=True)
                            continue

                        self.pair_count += 1
                        self.last_pair = pair

                        # Dispara callback do sniper
                        try:
                            result = self.callback(pair.address, pair.token0, pair.token1, dex_info=pair.dex)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error("Erro no callback", exc_info=True)
                            _notify(f"⚠️ Erro no callback: {e}", via_alert=True)

            except Exception as e:
                logger.error("Erro no discovery loop", exc_info=True)
                _notify(f"⚠️ Erro discovery: {e}", via_alert=True)

            await asyncio.sleep(self.interval)

    def _parse_log(self, dex: DexInfo, log: LogReceipt) -> Optional[PairInfo]:
        """
        Constrói e retorna um PairInfo a partir de um log de criação de par.
        """
        try:
            t0 = self._decode_addr(log["topics"][1])
            t1 = self._decode_addr(log["topics"][2])
            raw = log["data"].hex() if hasattr(log["data"], "hex") else log["data"]
            body = raw[2:] if raw.startswith("0x") else raw
            word = body[0:64] if dex.type == "v2" else body[-64:]
            addr = self._decode_addr(bytes.fromhex(word))
            logger.info(f"[{dex.name}] Par detectado: {addr} ({t0}/{t1})")
            return PairInfo(dex=dex, address=addr, token0=t0, token1=t1)
        except Exception as e:
            logger.warning(f"Falha ao parsear log {dex.name}: {e}")
            return None

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        """
        Verifica se a pool V2 possui liquidez mínima em WETH.
        """
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
            reserve = r0 if t0 == self.base_tokens[0].lower() else r1
            return reserve >= self.min_liq_wei
        except Exception:
            return False


# ─── API de Discovery ─────────────────────────────────────

_discovery: Optional[SniperDiscovery] = None


def subscribe_new_pairs(
    callback: Callable[..., Awaitable[Any]]
) -> None:
    """
    Registra seu callback on_new_pair(address, token0, token1, dex_info=DexInfo).
    Inicia o SniperDiscovery automaticamente com config do arquivo.
    """
    global _discovery
    if _discovery and _discovery.is_running():
        logger.warning("Discovery já iniciado")
        return

    # Carrega DEXes da configuração
    dexes_cfg = config.get("DEXES", [])
    dexes = [DexInfo(**d) for d in dexes_cfg]

    base = config.get("BASE_TOKENS", [])
    min_liq = Decimal(str(config.get("MIN_LIQ_WETH", 0.5)))
    interval = int(config.get("DISCOVERY_INTERVAL", 3))

    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    _discovery = SniperDiscovery(web3, dexes, base, min_liq, interval, callback)
    _discovery.start()


def is_discovery_running() -> bool:
    """
    Retorna True se o SniperDiscovery estiver ativo.
    """
    return bool(_discovery and _discovery.is_running())


def stop_discovery() -> None:
    """
    Para o SniperDiscovery em background.
    """
    if _discovery:
        _discovery.stop()


def run_discovery(
    web3: Web3,
    dexes: List[DexInfo],
    base_tokens: List[str],
    min_liq_weth: Decimal,
    interval_sec: int,
    callback: Callable[[PairInfo], Awaitable[Any]]
) -> None:
    """
    Inicializa e inicia o discovery com parâmetros específicos,
    caso precise customizar além do subscribe_new_pairs.
    """
    global _discovery
    if _discovery is None:
        _discovery = SniperDiscovery(web3, dexes, base_tokens, min_liq_weth, interval_sec, callback)
    _discovery.start()


def get_discovery_status() -> bool:
    """
    Informa se o discovery está rodando.
    """
    return bool(_discovery and _discovery.is_running())
