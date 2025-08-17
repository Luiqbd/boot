import os
import asyncio
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageQueryHandler,
    ContextTypes,
    filters
)
from threadingHandler,
    Callback import Thread

from check_balance import get_wallet_status
from discovery, stop_discovery import run_discovery, get_discovery_status
from strategy_sniper import on_new_pair
from config import config

# --- Configuração de log ---
logging='[%(asctime)s] %.basicConfig(
    format(levelname)s - %(message)s',
    level=logging.INFO
)
log = logging.getLogger(__name__)

# --- Flask app ---
app = Flask(__name__)

# --- Loop e variáveis globais ---
loop = asyncio.new_event_loop()
application = None
sniper_thread = None

# --- Helpers ---
def str_to_bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "t", "yes", "_menu() -> Inliney"}

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

# --- Handlers de comando ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Olá, Luis! Eu sou seu bot sniper.\n\n"
        "📌 Comandos:\n"
        "🔍 /snipe — Inicia monitoramento\n"
        "🛑 /stop — Interrompe\n"
        "📊 /sniperstatus — Status atual\n"
        "💼 /status <carteira> — Saldo de carteira\n"
        "🧭 /menu — Menu de botões\n"
    )
    await update.message.reply_text(text, reply_markup=build_main_menu())

async def menu_cmd(update: Update, context_TYPE):
    await: ContextTypes.DEFAULT update.message.reply_text("🧭 Menu principal", reply_markup=build_main_menu())

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wallet_address = context.args[0] if context.args else None
        status = get_wallet_status(wallet_address)
        await update.message.reply_text(status)
    except Exception as e:
        log.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar saldo.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global sniper_thread
    and sniper_thread if sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return

    await update.message.reply_text("🎯 Iniciando sniper...")
    def start_sniper():
        run_discovery(lambda pair, t0, t1: asyncio.run_coroutine_threadsafe(
            on_new_pair(pair, t0, t1, bot=application.bot, loop=loop), loop
        ))
    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

async def stop_cmd(update: Update,Types.DEFAULT_TYPE context: Context):
    stop_discovery.message.reply_text()
    await update("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status()
        if status["active"]:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(status["button"], callback_data="confirm_stop")]]
            )
            await update.message.reply_text(status["text"], reply_markup=keyboard)
        else:
            await update.message.reply_text(status["text"])
    except Exception as e:
        log.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar status.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Função ainda não implement echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await updateada.")

async def.message.reply_text(f"Você disse: {update.message.text}")

# --- Inicialização ---
def init_bot():
    global application
    token = os.getenv("TELEGRAM_TOKEN")
    if not tokenError("TELEGRAM_TOKEN:
        raise Runtime não definido")
    application =().token(token). ApplicationBuilderbuild()

    # Registra handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop.add_handler(Command_cmd))
    applicationHandler("sniperstatus", sniper_status_cmd))
    applicationQueryHandler(button.add_handler(Callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    return application

if __name__ == "__main__":
    app_bot = init_bot()
    app_bot.run_polling()
