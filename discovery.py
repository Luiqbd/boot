import asyncio
import logging
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
    type: str    # "v2" ou "v3"


@dataclass
class PairInfo:
    dex: DexInfo
    address: str
    token0: str
    token1: str


class SniperDiscovery:
    """
    Serviço de descoberta de novos pares/pools.
    callback_on_pair: chamada (sync ou async) ao detectar um par válido.
    """
    def __init__(
        self,
        web3: Web3,
        dexes: List[DexInfo],
        base_tokens: List[str],
        min_liq_weth: Decimal,
        interval_sec: int,
        callback_on_pair: Callable[[PairInfo], Awaitable[Any] | None],
    ):
        self.web3 = web3
        self.dexes = dexes
        self.base_tokens = [Web3.to_checksum_address(t) for t in base_tokens]
        self.min_liq_wei = int(min_liq_weth * Decimal(10**18))
        self.interval = interval_sec
        self.callback = callback_on_pair

        self._stop_event = asyncio.Event()
        self._last_block: Dict[str, int] = {}
        self._start_time: float = 0.0

        self.pair_count = 0
        self.pnl_total = Decimal("0")
        self.last_pair: Optional[PairInfo] = None

        # Assinaturas de evento para filtros
        self.SIG_V2 = Web3.to_hex(Web3.keccak(text="PairCreated(address,address,address,uint256)"))
        self.SIG_V3 = Web3.to_hex(Web3.keccak(text="PoolCreated(address,address,uint24,int24,address)"))

    def _decode_address(self, hexstr: str) -> str:
        """Extrai 20 bytes do final de uma topic/data word e retorna checksum."""
        return Web3.to_checksum_address("0x" + hexstr[-40:])

    def _init_blocks(self) -> None:
        current = self.web3.eth.block_number
        for dex in self.dexes:
            self._last_block[dex.name] = current

    def start(self) -> None:
        """Inicia o loop de descoberta em background."""
        if self._start_time:
            logger.warning("SniperDiscovery já rodando")
            return

        self._start_time = time.time()
        self._stop_event.clear()
        self._init_blocks()
        asyncio.create_task(self._run_loop())
        send_report(
            bot=Web3().eth.account,  # substitua por seu Bot/Chat
            message="🔍 Sniper iniciado! Monitorando novas DEXes..."
        )
        logger.info("🔍 SniperDiscovery iniciado")

    def stop(self) -> None:
        """Solicita parada graciosa do discovery."""
        self._stop_event.set()
        logger.info("🛑 SniperDiscovery interrompido manualmente")

    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    def status(self) -> Dict[str, Any]:
        """Retorna o status atual (uptime, pares, PnL, último par)."""
        if not self.is_running():
            return {"active": False, "text": "🔴 Sniper parado.", "button": None}

        up = int(time.time() - self._start_time)
        m, s = divmod(up, 60)
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
        """Loop principal que busca logs e aciona callback."""
        while not self._stop_event.is_set():
            try:
                latest = self.web3.eth.block_number
                for dex in self.dexes:
                    from_b = self._last_block[dex.name] + 1
                    if latest < from_b:
                        continue

                    sig = self.SIG_V2 if dex.type == "v2" else self.SIG_V3
                    logs: List[LogReceipt] = self.web3.eth.get_logs({
                        "fromBlock": from_b,
                        "toBlock": latest,
                        "address": dex.factory,
                        "topics": [sig]
                    })
                    self._last_block[dex.name] = latest

                    for log in logs:
                        pair = self._parse_log(dex, log)
                        if not pair:
                            continue

                        # só segue se houver token-base
                        if not {pair.token0, pair.token1} & set(self.base_tokens):
                            continue

                        await self._notify_new_pair(pair)
                        if not await self._has_min_liq(pair):
                            await self._notify(f"⏳ Sem liquidez mínima: {pair.address}")
                            continue

                        # par válido: dispara callback
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

    def _parse_log(self, dex: DexInfo, log: LogReceipt) -> Optional[PairInfo]:
        """
        Extrai token0, token1 e endereço do par do log:
         - V2: endereço em data[0]
         - V3: endereço em data[-1]
        """
        try:
            t0 = self._decode_address(log["topics"][1].hex())
            t1 = self._decode_address(log["topics"][2].hex())
            data = log["data"]
            hexdata = data.hex() if hasattr(data, "hex") else str(data)
            body = hexdata[2:] if hexdata.startswith("0x") else hexdata

            if dex.type == "v2":
                addr_word = body[0:64]
            else:
                addr_word = body[-64:]

            pair_addr = self._decode_address(addr_word)
            logger.info(f"[{dex.name}] Novo par detectado: {pair_addr} ({t0}/{t1})")
            return PairInfo(dex=dex, address=pair_addr, token0=t0, token1=t1)

        except Exception as e:
            logger.warning(f"Falha ao parsear log em {dex.name}: {e}")
            return None

    async def _has_min_liq(self, pair: PairInfo) -> bool:
        """
        Verifica liquidez mínima via RPC simples (somente V2).
        Para V3, integrar DexClient ou lógica adequada.
        """
        if pair.dex.type == "v2":
            try:
                pair_contract = self.web3.eth.contract(
                    address=pair.address,
                    abi=[  # ABI mínima para getReserves/token0/token1
                        {
                            "inputs": [],
                            "name": "getReserves",
                            "outputs": [
                                {"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}
                            ],
                            "stateMutability": "view",
                            "type": "function",
                        },
                        {"inputs": [], "name": "token0", "outputs":[{"type":"address"}], "type":"function"},
                        {"inputs": [], "name": "token1", "outputs":[{"type":"address"}], "type":"function"},
                    ]
                )
                r0, r1, _ = pair_contract.functions.getReserves().call()
                # escolhe reserva de WETH
                t0 = pair_contract.functions.token0().call().lower()
                weth = self.base_tokens[0].lower()
                reserve_weth = r0 if t0 == weth else r1
                return int(reserve_weth) >= self.min_liq_wei

            except Exception:
                return False

        # para v3, recomenda-se usar DexClient.calc_dynamic_slippage ou _has_min_liquidity lá
        return True

    async def _notify_new_pair(self, pair: PairInfo) -> None:
        """Envia mensagem de descoberta de par."""
        text = (
            f"🆕 [{pair.dex.name}] Novo par:\n"
            f"{pair.address}\nTokens: {pair.token0} / {pair.token1}"
        )
        await self._notify(text)

    async def _notify(self, msg: str) -> None:
        """Encaminha alerta ao Telegram."""
        send_report(
            bot=Web3().eth.account,  # substitua por instância de Bot
            message=msg
        )


# ----- Exemplo de uso -----
if __name__ == "__main__":
    from telegram import Bot
    from dex import DexClient
    from trade_executor import TradeExecutor

    # inicializações
    web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))
    bot = Bot(token=config["TELEGRAM_TOKEN"])

    # callback de compra/venda
    async def on_pair(pair: PairInfo):
        client = TradeExecutor(exchange_client=..., dry_run=config["DRY_RUN"])
        # aqui você faria client.buy(...) ou sell(...)

    # monta lista de DexInfo
    dexes = [
        DexInfo(name=d["name"], factory=d["factory"], type=d["type"])
        for d in config["DEXES"]
    ]
    base_tokens = [config["WETH"], config["USDC"]]
    discovery = SniperDiscovery(
        web3=web3,
        dexes=dexes,
        base_tokens=base_tokens,
        min_liq_weth=Decimal(config.get("MIN_LIQ_WETH", "1.0")),
        interval_sec=config["INTERVAL"],
        callback_on_pair=on_pair
    )

    discovery.start()

    # para rodar indefinidamente (CTRL+C para sair):
    try:
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        discovery.stop()
