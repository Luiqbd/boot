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
telegram_loop: asyncio.Abstract
