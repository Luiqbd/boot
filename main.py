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
        run_discovery(lambda pair, t0, t1: on_new_pair(pair, t0, t1, bot=application.bot))

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

    if data == "confirm_stop":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sim, parar", callback_data="stop_sniper"),
            InlineKeyboardButton("❌ Cancelar", callback_data="cancel_stop")
        ]])
        await query.edit_message_text("❓ Tem certeza que deseja parar o sniper?", reply_markup=keyboard)

    elif data == "stop_sniper":
        stop_discovery()
        await query.edit_message_text("🛑 Sniper interrompido com sucesso.", reply_markup=build_main_menu())

    elif data == "cancel_stop":
        await query.edit_message_text("⏳ Ação cancelada. Sniper continua rodando.", reply_markup=build_main_menu())

    elif data == "show_addr":
        try:
            addr = get_active_address()
            await query.edit_message_text(f"🔑 Endereço ativo: {addr}", reply_markup=build_main_menu())
        except Exception as e:
            await query.edit_message_text(f"⚠️ Não foi possível obter o endereço: {e}", reply_markup=build_main_menu())

    elif data == "show_env":
        try:
            text = env_summary_text()
            await query.edit_message_text(text, reply_markup=build_main_menu())
        except Exception as e:
            await query.edit_message_text(f"⚠️ Erro ao ler configuração: {e}", reply_markup=build_main_menu())

    elif data == "show_balance_self":
        try:
            addr = get_active_address()
            status = get_wallet_status(addr)
            await query.edit_message_text(status, reply_markup=build_main_menu())
        except Exception as e:
            await query.edit_message_text(f"⚠️ Erro ao consultar saldo: {e}", reply_markup=build_main_menu())

    elif data == "show_sniper_status":
        try:
            status = get_discovery_status()
            if status["active"]:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(status["button"], callback_data="confirm_stop")],
                    [InlineKeyboardButton("🧭 Voltar ao menu", callback_data="back_to_menu")]
                ])
                await query.edit_message_text(status["text"], reply_markup=keyboard)
            else:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🧭 Voltar ao menu", callback_data="back_to_menu")]
                ])
                await query.edit_message_text(status["text"], reply_markup=keyboard)
        except Exception as e:
            await query.edit_message_text(f"⚠️ Erro ao verificar status do sniper: {e}", reply_markup=build_main_menu())

    elif data == "back_to_menu":
        await query.edit_message_text("🧭 Menu principal", reply_markup=build_main_menu())

# --- Handler para mensagens comuns ---
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

# --- Endpoint do webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(
            application.process_update(update),
            loop
        )
        return 'ok', 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info=True)
        return 'error', 500

# --- Registro do webhook com retry ---
def set_webhook_with_retry(max_attempts=5, delay=3):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for attempt in range(1, max_attempts + 1):
        resp = requests.post(url, json={"url": WEBHOOK_URL})
        if resp.status_code == 200 and resp.json().get("ok"):
            logging.info(f"✅ Webhook registrado com sucesso: {WEBHOOK_URL}")
            return
        logging.warning(f"Tentativa {attempt} falhou: {resp.text}")
        time.sleep(delay)
    logging.error("❌ Todas as tentativas de registrar o webhook falharam.")

# --- Iniciar Flask em thread separada ---
def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Inicialização principal ---
if __name__ == "__main__":
    asyncio.set_event_loop(loop)

    # Criar bot Telegram
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Função assíncrona para iniciar o bot corretamente
    async def start_bot():
        await application.initialize()
        await application.start()

    # Iniciar bot no loop principal
    loop.create_task(start_bot())

    # Iniciar Flask em thread separada
    flask_thread = Thread(target=start_flask)
    flask_thread.start()

    # Registrar webhook com retry
    Thread(target=set_webhook_with_retry).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
