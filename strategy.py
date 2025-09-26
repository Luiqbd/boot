# strategy.py
import os
import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None
from config import config

logger = logging.getLogger(__name__)

LAST_PRICE_FILE = "last_price.json"
TRADES_LOG_FILE = "trades.jsonl"

# Utilitários de persistência e log
def _load_last_price() -> Optional[float]:
    try:
        if os.path.exists(LAST_PRICE_FILE):
            with open(LAST_PRICE_FILE, "r") as f:
                return float(json.load(f).get("last_price"))
    except:
        return None
    return None

def _save_last_price(price: float):
    try:
        with open(LAST_PRICE_FILE, "w") as f:
            json.dump({"last_price": price}, f)
    except Exception as e:
        logger.warning(f"Não foi possível salvar last_price: {e}")

def _append_trade_log(entry: dict):
    try:
        entry = {"timestamp": datetime.utcnow().isoformat(), **entry}
        with open(TRADES_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"📝 Trade registrado: {entry}")
    except Exception as e:
        logger.warning(f"Falha ao gravar log de trade: {e}")

class TradingStrategy:
    def __init__(self, dex_client, trader, alert):
        self.dex = dex_client
        self.trader = trader
        self.alert = alert

        self.web3 = getattr(self.dex, "web3", None) or Web3(Web3.HTTPProvider(config["RPC_URL"]))
        self.dry_run = bool(config.get("DRY_RUN", True))
        self.trade_size_eth = float(config.get("TRADE_SIZE_ETH", 0.02))
        self.weth = Web3.to_checksum_address(config["WETH"])
        self.router = Web3.to_checksum_address(config["DEX_ROUTER"])
        self.usdc = Web3.to_checksum_address(os.getenv("USDC_BASE", "")) if os.getenv("USDC_BASE") else None

        self.tp_pct = float(config.get("TAKE_PROFIT_PCT", 0.03))
        self.sl_pct = float(config.get("STOP_LOSS_PCT", 0.02))
        self.trail_pct = float(config.get("TRAIL_PCT", 0.01))

        self.last_price = _load_last_price()

    def _get_amounts_out(self, amount_in_wei: int, path: list[str]) -> int:
        abi = [{
            "name": "getAmountsOut", "type": "function", "stateMutability": "view",
            "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "path", "type": "address[]"}],
            "outputs": [{"type": "uint256[]"}]
        }]
        r = self.web3.eth.contract(address=self.router, abi=abi)
        return r.functions.getAmountsOut(amount_in_wei, path).call()[-1]

    def _get_eth_price_usdc(self) -> Optional[float]:
        if not self.usdc:
            return None
        try:
            out = self._get_amounts_out(10**18, [self.weth, self.usdc])
            return out / 1e6  # USDC com 6 casas
        except Exception as e:
            logger.warning(f"Falha ao obter preço ETH/USDC: {e}")
            return None

    async def run(self):
        price = self._get_eth_price_usdc()
        if price is None:
            await self._notify_once("ℹ️ Defina USDC_BASE no ambiente para habilitar cotação ETH/USDC.")
            return

        if self.last_price is None:
            self.last_price = price
            _save_last_price(price)
            await self._notify(f"📂 Referência inicial definida: ${price:.2f}")
            return

        logger.info(f"💹 ETH agora: ${price:.2f} | Última ref: ${self.last_price:.2f}")

        if price <= self.last_price * 1.05:
            await self._execute_trade_cycle(price)
        else:
            logger.info("🕒 Sem sinal no momento.")

    async def _execute_trade_cycle(self, entry_price: float):
        tp_price = entry_price * (1 + self.tp_pct)
        sl_price = entry_price * (1 - self.sl_pct)
        highest_price = entry_price

        await self._notify(
            f"🎯 Trade iniciado\n"
            f"- Entrada: ${entry_price:.2f}\n"
            f"- TP: ${tp_price:.2f} | SL: ${sl_price:.2f} | Trail: {self.trail_pct*100:.1f}%"
        )

        # Compra
        if self.dry_run:
            tx_hash = await self.trader.market_buy(token_address=self.weth, amount_eth=self.trade_size_eth)
        else:
            amt_in_wei = self.web3.to_wei(self.trade_size_eth, "ether")
            amt_out_min = int(self._get_amounts_out(amt_in_wei, [self.weth, self.usdc]) * 0.98)
            deadline = int(time.time()) + int(config.get("TX_DEADLINE_SEC", 45))
            tx_hash = self.dex.buy_v2(amt_in_wei, amt_out_min, [self.weth, self.usdc], deadline)

        _append_trade_log({
            "type": "buy",
            "price_usd": entry_price,
            "amount_eth": self.trade_size_eth,
            "tx_hash": tx_hash,
            "success": bool(tx_hash)
        })

        if tx_hash:
            self.last_price = entry_price
            _save_last_price(entry_price)

        # Monitoramento
        while True:
            await asyncio.sleep(3)
            price = self._get_eth_price_usdc()
            if not price:
                continue

            if price > highest_price:
                highest_price = price
                sl_price = highest_price * (1 - self.trail_pct)
                await self._notify(f"📈 Novo topo: ${highest_price:.2f} | SL ajustado: ${sl_price:.2f}")

            if price >= tp_price:
                await self._on_exit_signal(price, reason="Take Profit")
                break
            if price <= sl_price:
                await self._on_exit_signal(price, reason="Stop Loss / Trailing Stop")
                break

    async def _on_exit_signal(self, price: float, reason: str):
        pnl_pct = ((price - self.last_price) / self.last_price) * 100 if self.last_price else 0.0

        await self._notify(
            f"🏁 Saída: {reason}\n"
            f"- Preço: ${price:.2f}\n"
            f"- PnL: {pnl_pct:.2f}%"
        )

        if self.dry_run:
            tx_hash = await self.trader.market_sell(token_address=self.usdc, amount_token=self.trade_size_eth)
        else:
            amt_in_wei = self.web3.to_wei(self.trade_size_eth, "ether")
            amt_out_min = int(self._get_amounts_out(amt_in_wei, [self.usdc, self.weth]) * 0.98)
            deadline = int(time.time()) + int(config.get("TX_DEADLINE_SEC", 45))
            tx_hash = self.dex.sell_v2(amt_in_wei, amt_out_min, [self.usdc, self.weth], deadline)

        _append_trade_log({
            "type": "sell",
            "price_usd": price,
            "amount_eth": self.trade_size_eth,
            "pnl_pct": pnl_pct,
            "tx_hash": tx_hash,
            "success": bool(tx_hash)
        })

        if tx_hash:
            await self._notify(f"✅ Venda concluída — PnL {pnl_pct:.2f}%")
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
        if text not in self._notified_flags:
            self._notified_flags.add(text)
            await self._notify(text)
