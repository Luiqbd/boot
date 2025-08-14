import os
import time
import threading
import asyncio
import logging

from flask import Flask, request
from web3 import Web3

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from strategy import TradingStrategy
from dex import DexClient
from config import config

# ----------------------
# Logging
# ----------------------
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ----------------------
# Configuração básica
# ----------------------
TOKEN = os.getenv("TELEGRAM_TOKEN") or config.get("TELEGRAM_TOKEN")
RPC_URL = config["RPC_URL"]
PRIVATE_KEY = config["PRIVATE_KEY"]
INTERVAL = int(os.getenv("INTERVAL", config.get("INTERVAL", 10)))

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN não definido no ambiente ou config")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://boot-no4o.onrender.com").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}"

# ----------------------
# Telegram Application
# ----------------------
application = ApplicationBuilder().token(TOKEN).build()
telegram_loop: asyncio.AbstractEventLoop | None = None

# ----------------------
# Handlers do Telegram
# ----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala, Luis! Seu bot tá online via webhook 🚀\nUse /wallet para ver o saldo.")

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        web3 = Web3(Web3.HTTPProvider(RPC_URL))
        address = web3.eth.account.from_key(PRIVATE_KEY).address
        checksum_address = web3.toChecksumAddress(address)

        # Saldo ETH
        balance = web3.eth.get_balance(checksum_address)
        eth_balance = web3.from_wei(balance, 'ether')

        # Saldo TOSHI
        token_address = web3.toChecksumAddress("0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4")
        decimals = 18
        abi = [{
            "constant": True,
            "inputs": [{"name": "_owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"name": "balance", "type": "uint256"}],
            "type": "function"
        }]
        contract = web3.eth.contract(address=token_address, abi=abi)
        raw_balance = contract.functions.balanceOf(checksum_address).call()
        toshi_balance = raw_balance / (10 ** decimals)

        await update.message.reply_text(
            f"🪪 Endereço: `{checksum_address}`\n"
            f"💰 ETH: {eth_balance:.6f}\n"
            f"🔸 TOSHI: {toshi_balance:.4f}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.exception("Erro no /wallet: %s", e)
        await update.message.reply_text(f"❌ Erro ao verificar carteira: {str(e)}")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("wallet", wallet))

# ----------------
