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
        telegram_loop: asyncio.AbstractEventLoop,
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = [Web3.to_checksum_address(t) for t in base_tokens]
        self.min_liq_wei = int(min_liq_weth * Decimal(10**18))
        self.interval = interval_sec
        self.bot = bot
        self.callback = callback_on_pair

        # loop do Telegram, para todos os send_report
        self._tg_loop = telegram_loop

        # threading.Event para sinalizar parada
        self._stop_event = threading.Event()
        self._last_block: Dict[str, int] = {}
        self._start_time: float = 0.0

        self.pair_count = 0
        self.pnl_total = Decimal("0")
        self.last_pair: Optional[PairInfo] = None

        # sinais para logs de evento
        self.SIG_V2 = Web3.to_hex(
            Web3.keccak(text="PairCreated(address,address,address,uint256)")
        )
        self.SIG_V3 = Web3.to_hex(
            Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)")
        )

        # loop exclusivo para discovery
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

        # cria e guarda um loop exclusivo para o discovery
        self._loop = asyncio.new_event_loop()

        def _run_loop_in_thread():
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._run_loop())
            self._loop.run_forever()

        # dispara o loop em thread daemon
        thread = threading.Thread(target=_run_loop_in_thread, daemon=True)
        thread.start()

        # envia alerta de “sniper iniciado” via o loop do Telegram
        asyncio.run_coroutine_threadsafe(
            send_report(
                bot=self.bot,
                message="🔍 Sniper iniciado! Monitorando novas DEXes..."
            ),
            self._tg_loop
        )
        logger.info("🔍 SniperDiscovery iniciado")

    def stop(self) -> None:
        """
        Para o loop de discovery.
        """
        self._stop_event.set()
        if self._loop and self._loop.is_running():
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

                        # notifica novo par no Telegram
                        asyncio.run_coroutine_threadsafe(
                            send_report(
                                bot=self.bot,
                                message=(
                                    f"🆕 [{pair.dex.name}] Novo par:\n"
                                    f"{pair.address}\n"
                                    f"Tokens: {pair.token0} / {pair.token1}"
                                )
                            ),
                            self._tg_loop
                        )

                        if not await self._has_min_liq(pair):
                            # sem liquidez mínima
                            asyncio.run_coroutine_threadsafe(
                                send_report(
                                    bot=self.bot,
                                    message=f"⏳ Sem liquidez mínima: {pair.address}"
                                ),
                                self._tg_loop
                            )
                            continue

                        # atualização de métricas
                        self.pair_count += 1
                        self.last_pair = pair

                        # dispara callback sem bloquear
                        try:
                            res = self.callback(pair)
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception as e:
                            logger.error("Erro no callback", exc_info=True)
                            asyncio.run_coroutine_threadsafe(
                                send_report(
                                    bot=self.bot,
                                    message=f"⚠️ Erro no callback: {e}"
                                ),
                                self._tg_loop
                            )

            except Exception as e:
                logger.error("Erro no loop de discovery", exc_info=True)
                asyncio.run_coroutine_threadsafe(
                    send_report(
                        bot=self.bot,
                        message=f"⚠️ Erro no loop de discovery: {e}"
                    ),
                    self._tg_loop
                )

            await asyncio.sleep(self.interval)

    def _parse_log(self, dex: DexInfo, log: LogReceipt) -> Optional[PairInfo]:
        try:
            t0 = self._decode_address(log["topics"][1].hex())
            t1 = self._decode_address(log["topics"][2].hex())
            data = log["data"]
            hexdata = data.hex() if hasattr(data, "hex") else str(data)
            body = hexdata[2:] if hexdata.startswith("0x") else hexdata

            addr_word = body[0:64] if dex.type == "v2" else body[-64:]
            pair_addr = self._decode_address(addr_word)

            logger.info(f"[{dex.name}] Novo par detectado: {pair_addr} ({t0}/{t1})")
            return PairInfo(dex=dex, address=pair_addr, token0=t0, token1=t1)

        except Exception as e:
            logger.warning(f"Falha ao parsear log em {dex.name}: {e}")
            return None

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        if pair.dex.type == "v2":
            try:
                pair_contract = self.web3.eth.contract(
                    address=pair.address,
                    abi=[
                        {
                            "inputs": [],
                            "name": "getReserves",
                            "outputs": [
                                {"type": "uint112"},
                                {"type": "uint112"},
                                {"type": "uint32"}
                            ],
                            "stateMutability": "view",
                            "type": "function",
                        },
                        {
                            "inputs": [],
                            "name": "token0",
                            "outputs": [{"type": "address"}],
                            "type": "function"
                        },
                        {
                            "inputs": [],
                            "name": "token1",
                            "outputs": [{"type": "address"}],
                            "type": "function"
                        },
                    ]
                )
                r0, r1, _ = pair_contract.functions.getReserves().call()
                t0 = pair_contract.functions.token0().call().lower()
                weth = self.base_tokens[0].lower()
                reserve_weth = r0 if t0 == weth else r1
                return reserve_weth >= self.min_liq_wei
            except Exception:
                return False
        return True


# ===========================
# Funções de controle externo
# ===========================

_discovery_instance: Optional[SniperDiscovery] = None


def run_discovery(
    web3: Web3,
    dexes: List[DexInfo],
    base_tokens: List[str],
    min_liq_weth: Decimal,
    interval_sec: int,
    bot: Any,
    callback_on_pair: Callable[[PairInfo], Awaitable[Any] | None],
    telegram_loop: asyncio.AbstractEventLoop,
) -> None:
    """
    Inicializa e inicia o discovery se ainda não estiver rodando.
    """
    global _discovery_instance
    if _discovery_instance is None:
        _discovery_instance = SniperDiscovery(
            web3=web3,
            dexes=dexes,
            base_tokens=base_tokens,
            min_liq_weth=min_liq_weth,
            interval_sec=interval_sec,
            bot=bot,
            callback_on_pair=callback_on_pair,
            telegram_loop=telegram_loop,
        )
    _discovery_instance.start()


def stop_discovery() -> None:
    """
    Solicita parada do discovery em background.
    """
    if _discovery_instance:
        _discovery_instance.stop()


def get_discovery_status() -> bool:
    """
    Retorna True se o discovery estiver ativo.
    """
    return _discovery_instance.is_running() if _discovery_instance else False
