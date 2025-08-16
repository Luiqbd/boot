# strategy.py
import os
import json
import logging
from datetime import datetime
from typing import Optional

from web3 import Web3
from config import config

logger = logging.getLogger(__name__)

LAST_PRICE_FILE = "last_price.json"
TRADES_LOG_FILE = "trades.jsonl"

def _load_last_price() -> Optional[float]:
    try:
        if os.path.exists(LAST_PRICE_FILE):
            with open(LAST_PRICE_FILE, "r") as f:
                data = json.load(f)
                return float(data.get("last_price"))
    except Exception:
        return None
    return None

def _save_last_price(price: float) -> None:
    try:
        with open(LAST_PRICE_FILE, "w") as f:
            json.dump({"last_price": price}, f)
    except Exception as e:
        logger.warning(f"Não foi possível salvar last_price: {e}")

def _append_trade_log(entry: dict) -> None:
    try:
        entry = {"timestamp": datetime.utcnow().isoformat(), **entry}
        with open(TRADES_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"📝 Trade registrado: {entry}")
    except Exception as e:
        logger.warning(f"Falha ao gravar log de trade: {e}")

class TradingStrategy:
    """
    Esqueleto compatível com o main.py:
      - Recebe DexClient, Trader (PaperTrader ou real) e TelegramAlert.
      - Expõe método assíncrono run() chamado no loop.
    Você pode evoluir os métodos marcados como TODO para colocar a lógica real de entrada/saída.
    """

    def __init__(self, dex_client, trader, alert):
        self.dex = dex_client
        self.trader = trader
        self.alert = alert

        # Tenta reaproveitar web3 do Dex/Trader; se não houver, cria pelo config.
        self.web3 = getattr(self.dex, "web3", None) or getattr(self.trader, "web3", None)
        if self.web3 is None:
            self.web3 = Web3(Web3.HTTPProvider(config["RPC_URL"]))

        # Configs úteis
        self.dry_run = bool(config.get("DRY_RUN", True))
        self.trade_size_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
        self.weth = Web3.to_checksum_address(config["WETH"])
        self.router = Web3.to_checksum_address(config["DEX_ROUTER"])

        # Estado
        self.last_price = _load_last_price()

    def _get_amounts_out(self, amount_in_wei: int, path: list[str]) -> int:
        """
        Usa getAmountsOut do router (estilo Uniswap/Aerodrome) para cotação.
        """
        abi = [{
            "name": "getAmountsOut", "type": "function", "stateMutability": "view",
            "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
            "outputs": [{"name": "", "type": "uint256[]"}]
        }]
        r = self.web3.eth.contract(address=self.router, abi=abi)
        return r.functions.getAmountsOut(amount_in_wei, path).call()[-1]

    def _get_eth_price_in_usdc(self) -> Optional[float]:
        """
        Estima o preço do ETH em USDC via rota WETH->USDC na Base (você pode ajustar o token estável).
        Por padrão tenta USDC nativo da Base via variável USDC_BASE; se não setada, retorna None.
        """
        usdc_addr = os.getenv("USDC_BASE")
        if not usdc_addr:
            # Evita chutar endereço: peça para setar USDC_BASE no ambiente
            return None
        usdc = Web3.to_checksum_address(usdc_addr)
        one_weth = 10**18
        try:
            out = self._get_amounts_out(one_weth, [self.weth, usdc])
            # USDC tem 6 casas normalmente; convertemos para float USD/ETH
            return out / 1e6
        except Exception as e:
            logger.warning(f"Falha ao cotar ETH->USDC: {e}")
            return None

    async def run(self):
        """
        Loop de decisão minimalista:
          - Obtém preço (se USDC_BASE estiver configurado).
          - Define referência de last_price se vazia.
          - Gera um 'sinal' de exemplo quando há variação abaixo de +5% desde a última referência.
          - Em DRY_RUN: só alerta e loga; caso contrário, você pode acoplar execução real aqui.
        """
        price = self._get_eth_price_in_usdc()

        if price is not None:
            if self.last_price is None:
                self.last_price = price
                _save_last_price(price)
                logger.info(f"📂 Referência inicial definida: ${price:.2f}")
                await self._notify(f"📂 Referência de preço definida: ${price:.2f}")
                return

            logger.info(f"💹 ETH agora: ${price:.2f} | Última ref: ${self.last_price:.2f}")

            # Regra exemplo (placeholder): entrar se preço atual <= +5% da última referência
            should_enter = price <= self.last_price * 1.05

            if should_enter:
                await self._on_signal(price)
            else:
                logger.info("🕒 Sem sinal no momento.")
        else:
            # Se não temos USDC_BASE, mantemos um heartbeat leve para não travar o loop.
            logger.info("ℹ️ USDC_BASE não configurado. Defina a variável de ambiente para cotação ETH/USDC.")
            await self._notify_once("ℹ️ Defina USDC_BASE no ambiente para habilitar cotação ETH/USDC (Base).")

    async def _on_signal(self, price: float):
        """
        O que fazer quando a condição de entrada aciona.
        Aqui está em modo seguro: apenas alerta e registra. Você pode plugar execução real abaixo.
        """
        msg = (
            f"🚦 Sinal de entrada\n"
            f"- Preço ETH/USDC: ${price:.2f}\n"
            f"- Tamanho (ETH): {self.trade_size_eth:.6f}\n"
            f"- DRY_RUN: {self.dry_run}"
        )
        await self._notify(msg)

        # Log estruturado
        _append_trade_log({
            "type": "signal_buy",
            "price_usd": price,
            "amount_eth": self.trade_size_eth,
            "success": True
        })

        if self.dry_run:
            logger.info("🔬 DRY_RUN ativado — sinal registrado sem execução.")
            return

        # Ponto de integração para execução real (ajuste conforme seu Trader/Dex):
        # Exemplo apenas ilustrativo; adapte à API real do seu trader/dex.
        try:
            # Se existir um método explícito no trader, você pode chamar aqui.
            # Ex.: tx_hash = await self.trader.market_buy_weth(amount_eth=self.trade_size_eth)
            # Para manter compatível sem quebrar, só notificamos por enquanto:
            await self._notify("⚙️ Execução real não configurada nesta versão. Adapte o método do Trader aqui.")
        except Exception as e:
            logger.exception(f"Falha na execução real: {e}")
            await self._notify(f"❌ Falha na execução: {e}")
        else:
            # Se executar de fato, atualize referência e logue:
            self.last_price = price
            _save_last_price(price)

    async def _notify(self, text: str):
        try:
            if self.alert:
                await self.alert.send(text)
            else:
                logger.info(text)
        except Exception as e:
            logger.warning(f"Falha ao notificar no Telegram: {e}")

    _notified_flags = set()
    async def _notify_once(self, text: str):
        if text in self._notified_flags:
            return
        self._notified_flags.add(text)
        await self._notify(text)
