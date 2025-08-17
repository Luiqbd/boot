import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from threading import Thread
import time
from web3 import Web3

# --- Importações sniper ---
from check_balance import get_wallet_status
from discovery import run_discovery, stop_discovery, get_discovery_status

# --- Camada de execução segura ---
from dex import DexClient
from exchange_client import ExchangeClient
from trade_executor import TradeExecutor
from safe_trade_executor import SafeTradeExecutor
from risk_manager import RiskManager
from config import config

# --- Estratégia sniper original ---
from strategy_sniper import on_new_pair  # adaptaremos chamada

# --- Configuração de log ---
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- Flask app ---
app = Flask(__name__)

# --- Variáveis globais ---
loop = asyncio.new_event_loop()
application = None
sniper_thread = None

# --- Configurações via ambiente ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]

# --- Helpers ---
def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY não definida no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("PRIVATE_KEY inválida: formato incorreto.")
    return pk

def get_active_address() -> str:
    pk_raw = os.getenv("PRIVATE_KEY")
    pk = normalize_private_key(pk_raw)
    return Web3().eth.account.from_key(pk).address

def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Endereço ativo", callback_data="show_addr"),
            InlineKeyboardButton("⚙️ Configuração", callback_data="show_env")
        ],
        [
            InlineKeyboardButton("💼 Saldo da carteira", callback_data="show_balance_self"),
        ],
        [
            InlineKeyboardButton("📊 Status do sniper", callback_data="show_sniper_status"),
            InlineKeyboardButton("🛑 Parar sniper", callback_data="confirm_stop")
        ]
    ])

def env_summary_text() -> str:
    try:
        addr = get_active_address()
    except Exception as e:
        addr = f"Erro ao obter: {e}"

    chain_id = os.getenv("CHAIN_ID", "8453")
    rpc_url = os.getenv("RPC_URL", "https://mainnet.base.org")
    router = os.getenv("DEX_ROUTER", "")
    factory = os.getenv("DEX_FACTORY", "")
    weth = os.getenv("WETH", "0x4200000000000000000000000000000000000006")
    dry_run = str_to_bool(os.getenv("DRY_RUN", "true"))
    trade_size = os.getenv("TRADE_SIZE_ETH", "0.01")
    slippage_bps = os.getenv("SLIPPAGE_BPS", "50")
    tx_deadline = os.getenv("TX_DEADLINE_SEC", "300")
    interval = os.getenv("INTERVAL", "10")
    webhook = os.getenv("WEBHOOK_URL", "")

    return (
        "⚙️ Configuração atual\n"
        f"- Endereço ativo: {addr}\n"
        f"- CHAIN_ID: {chain_id}\n"
        f"- RPC_URL: {rpc_url}\n"
        f"- DEX_ROUTER: {router}\n"
        f"- DEX_FACTORY: {factory}\n"
        f"- WETH: {weth}\n"
        f"- DRY_RUN: {dry_run}\n"
        f"- TRADE_SIZE_ETH: {trade_size}\n"
        f"- SLIPPAGE_BPS: {slippage_bps}\n"
        f"- TX_DEADLINE_SEC: {tx_deadline}\n"
        f"- INTERVAL: {interval}\n"
        f"- WEBHOOK_URL: {webhook}"
    )

# --- Inicialização da camada de execução segura ---
web3_client = ExchangeClient()
dex_client = DexClient(web3_client.web3)
trade_executor = TradeExecutor(web3_client, dry_run=config.get("DRY_RUN", True))
risk_manager = RiskManager(
    capital_eth=1.0,
    max_exposure_pct=0.1,
    max_trades_per_day=10,
    loss_limit=3,
    daily_loss_pct_limit=0.15,
    cooldown_sec=30
)
safe_executor = SafeTradeExecutor(trade_executor, risk_manager, dex_client)

def handle_new_pair(pair, token0, token1, bot):
    """
    Chamado sempre que discovery encontrar um par novo.
    Aqui é feita a verificação de liquidez/honeypot/riscos e execução segura.
    """
    try:
        current_price = dex_client.get_token_price(token1)
        last_trade_price = None
        amount_eth = float(os.getenv("TRADE_SIZE_ETH", "0.01"))

        tx = safe_executor.buy(
            token0, token1, amount_eth,
            current_price, last_trade_price
        )

        if tx:
            logging.info(f"🚀 Compra executada: {tx}")
            bot.send_message(chat_id=os.getenv("TELEGRAM_CHAT_ID"), text=f"🚀 Compra executada: {tx}")
        else:
            logging.warning("⚠️ Trade bloqueado")
    except Exception as e:
        logging.error(f"Erro em handle_new_pair: {e}", exc_info=True)

# --- Handlers de comando ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Olá, Luis! Eu sou seu bot sniper na rede Base.\n\n"
        "📌 Comandos disponíveis:\n"
        "🔍 /snipe — Inicia o sniper e começa a monitorar novos pares com liquidez\n"
        "🛑 /stop — Interrompe o sniper imediatamente\n"
        "📊 /sniperstatus — Mostra o status atual do sniper (tempo, pares, último par)\n"
        "💼 /status <carteira> — Mostra o saldo de ETH e WETH da carteira informada\n"
        "🧭 /menu — Abre o menu com botões\n"
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧭 Menu principal", reply_markup=build_main_menu())

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wallet_address = context.args[0] if context.args else None
        status = get_wallet_status(wallet_address)
        await update.message.reply_text(status)
    except Exception as e:
        logging.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Ocorreu um erro ao verificar o status da carteira.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return

    await update.message.reply_text("🎯 Iniciando sniper... Monitorando novos pares com liquidez.")

    def start_sniper():
        run_discovery(lambda pair, t0, t1: handle_new_pair(pair, t0, t1, bot=application.bot))

    sniper_thread = Thread(target=start_sniper)
    sniper_thread.start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_discovery()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status()
        if status["active"]:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(status["button"], callback_data="confirm_stop")]
            ])
            await update.message.reply_text(status["text"], reply_markup=keyboard)
        else:
            await update.message.reply_text(status["text"])
    except Exception as e:
        logging.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Ocorreu um erro ao verificar o status do sniper.")

# --- Handler de botões inline ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    # (botões iguais aos do seu main original...)

# --- Handler para mensagens comuns ---
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")
