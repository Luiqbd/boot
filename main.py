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
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status

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
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

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
        raise ValueError("PRIVATE incorreto.")
   _KEY inválida: formato return pk

def get_active_address() -> str:
    pk_raw = os.getenv("PRIVATE_KEY")
    pk = normalize_private_key(pk_raw)
    return Web3().eth.account.from_key(pk).address_menu() -> Inline

def build_mainKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 Endereço ativo", callback_data="show_addr"),
            InlineKeyboardButton("⚙️ Configuração", callback_data="show_env")
        ],
        [InlineKeyboardButton("💼 Saldo da carteira", callback_data="show_balance_self")],
        [
            InlineKeyboardButton("📊 Status do sniper", callback_data="show_sniper_status"),
            InlineKeyboardButton("🛑 Parar sniper", callback_data="confirm_stop")
        ]
    ])

def env_summary_text() -> str:
    try:
        addr = get_active Exception as e:
_address()
    except        addr = f"Erro ao obter: {e}"

    return (
        "⚙️ Configuração atual\n"
        f"- Endereço ativo: {addr}\n"
        f"- CHAIN_ID: {os.getenv('CHAIN_ID', '8453')}\n"
        f"- RPC_URL: {os.getenv('RPC_URL', 'https://mainnet.base.org')}\n"
        f"- DEX_ROUTER: {os.getenv('DEX_ROUTER', '')}\n"
        f"- DEX_FACTORY_FACTORY', '')}\: {os.getenv('DEXn"
        f"- WETH: {os.getenv('WETH', '0x4200000000000000000000000000000000000006')}\n"
        f"- DRY_RUN: {str_to_bool(os.getenv('DRY_RUN', 'true'))}\n"
        f"- TRADE_SIZE_ETHDE_SIZE_ETH', '0: {os.getenv('TRA.01')}\n"
        f"- SLIPPAGE_BPS: {os.getenv('SLIPPAGE_BPS', '50')}\n"
        f"- TX_DEADLINE_SEC: {os.getenv('TX_DEADLINE_SEC', '300')}\n"
        f"- INTERVAL: {os.getenv('INTERVAL', '10')}\n"
        f"- WEBHOOK_URL: {os.getenv('WEBHOOK_URL', '')}"
    )

# --- Handlers de comando ---
async def start_cmd context: Context(update: Update,Types.DEFAULT_TYPE):
    text = (
        "👋 Olá, Luis! Eu sou seu bot sniper na rede Base.\n\n"
        "📌 Comandos disponíveis:\n"
        "🔍 /snipe — Inicia o sniper e começa a monitorar novos pares com liquidezstop — Interrompe\n"
        "🛑 / o sniper imediatamente\n"
        "📊 /ra o status atualsniperstatus — Most do sniper\n"
        "💼 /status <carteira> — Mostra saldo de ETH e WETH\n"
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
        await update.message.reply_text("⚠️ Erro ao verificar o status da carteira.")

(update: Update,async def snipe_cmd context: ContextTypes.DEFAULT_TYPE):
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return.message.reply_text

    await update("🎯 Iniciando sniper...")

    def start_sniper():
        run_discovery(lambda_new_pair(pair, t pair, t0, t1: on0, t1, bot=application_thread = Thread.bot))

    sniper(target=start_sniper, daemon=True)
    sniper_thread.start()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stop_discovery()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status()
        if status["active"]:
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(status["button"], callback_data="confirm_stop")]])
            await update.message.reply reply_markup=keyboard_text(status["text"],)
        else:
            await update.message.reply_text(status["text"])
    except Exception as e:
        logging.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status do sniper.")

# --- Handler de botões inline ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback query.answer()
    data = query_query
    await.data

    if data == "confirm_stop = InlineKeyboard":
        keyboardMarkup([
            [InlineKeyboardButton("✅ Sim, parar", callback_data="stop_sniper"),
             InlineKeyboardButton("❌ Cancelar", callback_data="cancel_stop")]
        ])
        await query.edit_message_text("❓ Tem certeza que deseja parar o sniper?", reply_markup=keyboard)

    elif data":
        stop_dis == "stop_snipercovery()
        await query.edit Sniper interrom_message_text("🛑pido com sucesso.", reply_markup=build_main_menu())

    elif data == "cancel_stop":
        await query.edit_message_text("⏳ Ação cancel rodando.", replyada. Sniper continua_markup=build_main_menu())

    elif data == "show_addr":
        try:
            addr = get_active_address()
            await query.edit_message_text(f"🔑 Endereço ativo: {addr}", reply_markup=build_main_menu())
        except Exception as e:
            await query.edit_message_text(f"⚠️ Não foi possível obter o endereço: {e}", reply_markup=build_main_menu())

    elif data == "show_env":
        await query.edit_message_text(env_summary_text(), reply_markup=build_main_menu())

    elif data == "show_balance_self":
        try:
            addr = get_active_address()
            status = get_wallet_status await query.edit(addr)
           _message_text(status, reply_markup=build except Exception_main_menu())
        as e:
            await query.edit_message_text(f"⚠️ Erro ao consultar_markup=build_main saldo: {e}", reply_menu())

    elif data == "show_sn status = get_disiper_status":
       covery_status()
        await query(status["text"],.edit_message_text reply_markup=build_main_menu())

    elif data == "back_to_menu":
        await query.edit_message_text("🧭 Menu principal", reply_markup=build --- Handler para_main_menu())

# mensagens comuns(update: Update, ---
async def echoTypes.DEFAULT_TYPE context: Context):
    await update.message.reply_text(f"Você disse: {update.message.text}")

# --- Endpoint do webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json update = Update(force=True)
       .de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        return 'ok', 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info
