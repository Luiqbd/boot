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
from telegram_alert import send_report

logger = logging.getLogger("discovery")


@dataclass(frozen=True)
class DexInfo:
    name: str
    factory: str
    router: str
    type: str    # "v2" ou "v3"


@dataclass
class PairInfo:
    dex: DexInfo
    address: str
    token0: str
    token1: str


class SniperDiscovery:
    """
    Serviço de descoberta de novos pares/pools em DEXes.
    Ao detectar um par válido, dispara `callback_on_pair(pair)`.
    """

    def __init__(
        self,
        web3: Web3,
        dexes: List[DexInfo],
        base_tokens: List[str],
        min_liq_weth: Decimal,
        interval_sec: int,
        bot: Any,
        callback_on_pair: Callable[[PairInfo], Awaitable[Any] | None],
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = [Web3.to_checksum_address(t) for t in base_tokens]
        self.min_liq_wei = int(min_liq_weth * Decimal(10**18))
        self.interval = interval_sec
        self.bot = bot
        self.callback = callback_on_pair

        # Agora usamos threading.Event para sinalizar parada
        self._stop_event = threading.Event()
        self._last_block: Dict[str, int] = {}
        self._start_time: float = 0.0

        self.pair_count = 0
        self.pnl_total = Decimal("0")
        self.last_pair: Optional[PairInfo] = None

        # Sinais para logs de evento
        self.SIG_V2 = Web3.to_hex(Web3.keccak(text="PairCreated(address,address,address,uint256)"))
        self.SIG_V3 = Web3.to_hex(Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)"))

        # Atributo para guardar referência ao loop
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _decode_address(self, hexstr: str) -> str:
        return Web3.to_checksum_address("0x" + hexstr[-40:])

    def _init_blocks(self) -> None:
        current = self.web3.eth.block_number
        for dex in self.dexes:
            self._last_block[dex.name] = current

    def start(self) -> None:
        """
        Inicia o loop de discovery em background.
        """
        if self._start_time:
            logger.warning("SniperDiscovery já está rodando")
            return

        self._start_time = time.time()
        self._stop_event.clear()
        self._init_blocks()

        # Cria um novo event loop exclusivo
        self._loop = asyncio.new_event_loop()

        def _run_loop_in_thread(loop: asyncio.AbstractEventLoop):
            # Define esse loop como padrão nesta thread
            asyncio.set_event_loop(loop)
            # Agenda a corrotina principal
            loop.create_task(self._run_loop())
            # Mantém o loop rodando
            loop.run_forever()

        # Inicia o loop em thread separada
        thread = threading.Thread(
            target=_run_loop_in_thread,
            args=(self._loop,),
            daemon=True,
        )
        thread.start()

        send_report(bot=self.bot, message="🔍 Sniper iniciado! Monitorando novas DEXes...")
        logger.info("🔍 SniperDiscovery iniciado")

    def stop(self) -> None:
        """
        Para o loop de discovery.
        """
        self._stop_event.set()
        if self._loop and self._loop.is_running():
            # Para o event loop de forma thread-safe
            self._loop.call_soon_threadsafe(self._loop.stop)
        logger.info("🛑 SniperDiscovery interrompido")

    def is_running(self) -> bool:
        """
        Retorna True se o discovery estiver ativo.
        """
        return not self._stop_event.is_set()

    def status(self) -> Dict[str, Any]:
        """
        Retorna dicionário com status atual: ativo, tempo, contagem e último par.
        """
        if not self.is_running():
            return {"active": False, "text": "🔴 Sniper parado.", "button": None}

        elapsed = int(time.time() - self._start_time)
        m, s = divmod(elapsed, 60)
        txt = (
            f"🟢 Ativo há {m}m{s}s\n"
            f"🔢 Pares encontrados: {self.pair_count}\n"
            f"💹 PnL simulado: {self.pnl_total:.4f} WETH\n"
        )
        if self.last_pair:
            p = self.last_pair
            txt += f"🆕 Último par: {p.address}\n  {p.token0[:6]}… / {p.token1[:6]}…"
        return {"active": True, "text": txt, "button": "🛑 Parar sniper"}

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
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
                        "topics": [sig]
                    })
                    self._last_block[dex.name] = latest

                    for log_entry in logs:
                        pair = self._parse_log(dex, log_entry)
                        if not pair:
                            continue

                        if not {pair.token0, pair.token1} & set(self.base_tokens):
                            continue

                        await self._notify_new_pair(pair)

                        if not await self._has_min_liq(pair):
                            await self._notify(f"⏳ Sem liquidez mínima: {pair.address}")
                            continue

                        self.pair_count += 1
                        self.last_pair = pair

                        try:
                            res = self.callback(pair)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as e:
                            logger.error("Erro no callback", exc_info=True)
                            await self._notify(f"⚠️ Erro no callback: {e}")

            except Exception as e:
                logger.error("Erro no loop de discovery", exc_info=True)
                await self._notify(f"⚠️ Erro no loop: {e}")

            await asyncio.sleep(self.interval)

    # ... o restante das funções (_parse_log, _has_min_liq, _notify_new_pair, etc.) permanece inalterado ...


# ===========================
# Funções de controle externo
# ===========================

_discovery_instance: Optional[SniperDiscovery] = None


def run_discovery(*args, **kwargs) -> None:
    global _discovery_instance
    if _discovery_instance is None:
        _discovery_instance = SniperDiscovery(*args, **kwargs)
    _discovery_instance.start()


def stop_discovery() -> None:
    if _discovery_instance:
        _discovery_instance.stop()


def get_discovery_status() -> bool:
    return _discovery_instance.is_running() if _discovery_instance else False
