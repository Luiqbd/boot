import os
import json
import asyncio
import logging
from flask import Flask, request
from web3 import Web3
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from strategy import TradingStrategy
from dex import DexClient
from paper_trader import PaperTrader
from telegram_alert import TelegramAlert
from config import config

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN") or config.get("TELEGRAM_TOKEN")
RPC_URL = config["RPC_URL"]
PRIVATE_KEY = config["PRIVATE_KEY"]
CHAT_ID = config["TELEGRAM_CHAT_ID"]
INTERVAL = int(os.getenv("INTERVAL", config.get("INTERVAL", 10)))

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN não definido no ambiente ou config")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://boot-no4o.onrender.com").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

web3 = Web3(Web3.HTTPProvider(RPC_URL))
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY não definida no ambiente ou config")
wallet_address = web3.eth.account.from_key(PRIVATE_KEY).address

application = ApplicationBuilder().token(TOKEN).build()

# --- Comandos Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala, Luis! 🤖 Bot online via webhook 🚀\n"
        "Comandos:\n"
        "/wallet → saldo de ETH e TOSHI\n"
        "/saldo → saldo de ETH e WETH\n"
        "/relatorio → desempenho dos trades"
    )

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        checksum_address = Web3.to_checksum_address(wallet_address)
        balance = web3.eth.get_balance(checksum_address)
        eth_balance = web3.from_wei(balance, 'ether')

        token_address = Web3.to_checksum_address("0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4")
        abi = [{
            "constant": True, "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }]
        contract = web3.eth.contract(address=token_address, abi=abi)
        raw_balance = contract.functions.balanceOf(checksum_address).call()
        toshi_balance = raw_balance / (10 ** 18)

        await update.message.reply_text(
            f"🪪 Endereço: `{checksum_address}`\n"
            f"💰 ETH: {eth_balance:.6f}\n"
            f"🔸 TOSHI: {toshi_balance:.4f}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Erro no /wallet: %s", e)
        await update.message.reply_text(f"❌ Erro ao verificar carteira: {str(e)}")

async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        checksum_address = Web3.to_checksum_address(wallet_address)
        eth_balance = web3.eth.get_balance(checksum_address) / 1e18

        weth_address = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
        abi = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                "inputs": [{"name": "owner", "type": "address"}],
                "outputs": [{"type": "uint256"}]}]
        weth_contract = web3.eth.contract(address=weth_address, abi=abi)
        weth_balance = weth_contract.functions.balanceOf(checksum_address).call() / 1e18

        await update.message.reply_text(
            f"📍 Carteira: `{checksum_address}`\n"
            f"💰 ETH: {eth_balance:.6f}\n"
            f"💰 WETH: {weth_balance:.6f}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Erro no /saldo: %s", e)
        await update.message.reply_text(f"❌ Erro ao verificar saldo: {str(e)}")

async def relatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    TRADES_LOG_FILE = "trades.jsonl"
    if not os.path.exists(TRADES_LOG_FILE):
        await update.message.reply_text("📂 Nenhum histórico de trade encontrado.")
        return

    total, ganhos, perdas, pnl_total = 0, 0, 0, 0.0
    with open(TRADES_LOG_FILE, "r") as f:
        for line in f:
            try:
                trade = json.loads(line)
                if trade.get("type") == "sell":
                    total += 1
                    pnl = trade.get("pnl_pct", 0.0)
                    pnl_total += pnl
                    if pnl > 0:
                        ganhos += 1
                    elif pnl < 0:
                        perdas += 1
            except json.JSONDecodeError:
                continue

    if total == 0:
        await update.message.reply_text("📂 Nenhuma venda registrada para gerar relatório.")
        return

    taxa_acerto = (ganhos / total) * 100
    pnl_medio = pnl_total / total

    msg = (
        f"📊 *Relatório de Trades*\n"
        f"- Operações fechadas: {total}\n"
        f"- 💹 Taxa de acerto: {taxa_acerto:.2f}%\n"
        f"- 📈 Ganhos: {ganhos} | 📉 Perdas: {perdas}\n"
        f"- 📊 PnL total: {pnl_total:.2f}%\n"
        f"- 📊 PnL médio: {pnl_medio:.2f}%"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# Registra comandos
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("wallet", wallet))
application.add_handler(CommandHandler("saldo", saldo))
application.add_handler(CommandHandler("relatorio", relatorio))

# Estratégia assíncrona
async def executar_bot_async(strategy):
    while True:
        try:
            await strategy.run()
        except Exception as e:
            logger.error("Erro na estratégia: %s", str(e))
        await asyncio.sleep(INTERVAL)

# Flask server
flask_app = Flask(__name__)
PORT = int(os.environ.get("PORT", 5000))

@flask_app.route("/", methods=["GET", "HEAD", "POST"])
def home():
    if request.method == "POST":
        return "ignored", 200
    return "✅ Bot está rodando com Flask + Webhook"

@flask_app.route("/status", methods=["GET"])
def status():
    return "🔍 Estratégia ativa, Telegram online."

@flask_app.route(WEBHOOK_PATH, methods=["POST", "GET"])
def webhook():
    if request.method == "GET":
        return "OK", 200
    try:
        data = request.get_json(force=True, silent=False)
        update = Update.de_json(data, application.bot)
        fut = asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            asyncio.get_event_loop()
        )
        fut.result(timeout=3)
        return "OK", 200
    except Exception as e:
        logger.exception("Erro no webhook: %s", e)
        return "Erro interno", 500

# Main
if __name__ == "__main__":
    logger.info("🚀 main.py iniciado")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def iniciar_bot():
        await application.initialize()
        await application.bot.set_webhook(WEBHOOK_URL)
        await application.start()
        logger.info("✅ Webhook registrado com sucesso")

        dex = DexClient(web3)
        trader = PaperTrader(web3, PRIVATE_KEY)
        alert = TelegramAlert(application.bot, CHAT_ID)
        strategy = TradingStrategy(dex, trader, alert)

        loop.create_task(executar_bot_async(strategy))
        flask_app.run(host="0.0.0.0", port=PORT)

    try:
        loop.run_until_complete(iniciar_bot())
    except Exception as e:
        logger.exception("Erro fatal no main: %s", e)
