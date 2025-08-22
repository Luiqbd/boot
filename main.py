import os
import asyncio
import logging
import requests
from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from threading import Thread
import time
import datetime
from web3 import Web3

# --- Importações sniper ---
from check_balance import get_wallet_status
from discovery import run_discovery, stop_discovery, get_discovery_status
from config import config

# --- RiskManager ---
from risk_manager import RiskManager
risk_manager = RiskManager()

# --- Função ao encontrar novo par ---
async def on_new_pair(dex_info, pair_addr, token0, token1, bot=None, loop=None):
    current_price = 1.0
    last_trade_price = 0.95
    trade_size_eth = 0.05
    direction = "buy"
    pair = (token0, token1)
    now_ts = int(datetime.datetime.now().timestamp())
    min_liquidity_ok = True
    not_honeypot = True

    pode_operar = risk_manager.can_trade(
        current_price=current_price,
        last_trade_price=last_trade_price,
        direction=direction,
        trade_size_eth=trade_size_eth,
        min_liquidity_ok=min_liquidity_ok,
        not_honeypot=not_honeypot,
        pair=pair,
        now_ts=now_ts
    )

    if not pode_operar:
        motivo = getattr(risk_manager, "last_block_reason", None)
        if motivo:
            msg = f"🚫 Compra bloqueada pelo RiskManager: {pair}\nMotivo: {motivo}"
        else:
            msg = f"🚫 Compra bloqueada pelo RiskManager: {pair}\nMotivo: não informado"
        logging.warning(msg)
        if bot:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        return

    sucesso = True
    risk_manager.register_trade(success=sucesso, pair=pair, direction=direction, now_ts=now_ts)
    pnl_simulado = 0.002
    risk_manager.register_pnl(pnl_simulado)

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

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# --- Funções auxiliares ---
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

def env_summary_text() -> str:
    try:
        addr = get_active_address()
    except Exception as e:
        addr = f"Erro ao obter: {e}"
    return (
        f"🔑 Endereço: `{addr}`\n"
        f"🌐 Chain ID: {os.getenv('CHAIN_ID')}\n"
        f"🔗 RPC: {os.getenv('RPC_URL')}\n"
        f"💵 Trade: {os.getenv('TRADE_SIZE_ETH')} ETH\n"
        f"📉 Slippage: {os.getenv('SLIPPAGE_BPS')} bps\n"
        f"🛑 Stop Loss: {os.getenv('STOP_LOSS_PCT')}%\n"
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')}%\n"
        f"💧 Min. Liquidez WETH: {os.getenv('MIN_LIQ_WETH')}\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')}s\n"
    )

# --- Funções sniper ---
def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return
    logging.info("⚙️ Iniciando sniper...")
    def start_sniper():
        try:
            run_discovery(
                lambda dex, pair, t0, t1: on_new_pair(dex, pair, t0, t1, bot=application.bot),
                loop
            )
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)
    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery(loop)

# --- Handlers de comando ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = (
        "🎯 **Bem-vindo ao Sniper Bot Criado por Luis Fernando**\n\n"
        "🟢 /snipe — Inicia o sniper\n"
        "🔴 /stop — Para o sniper\n"
        "📈 /sniperstatus — Status do sniper\n"
        "💰 /status — Saldo ETH/WETH\n"
        "🏓 /ping — Teste de vida\n"
        "🛰️ /testnotify — Notificação de teste\n"
        "📜 /menu — Reexibe este menu\n"
        "📊 /relatorio — Relatório do RiskManager\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛠 **Configuração Atual**\n"
        f"{env_summary_text()}"
    )
    await update.message.reply_text(mensagem, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wallet_address = context.args[0] if context.args else get_active_address()
        status = get_wallet_status(wallet_address)
        await update.message.reply_text(f"📊 Status da carteira `{wallet_address}`:\n{status}", parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status.")

async def relatorio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rel = risk_manager.gerar_relatorio()
        await update.message.reply_text(f"📊 Relatório de eventos:\n{rel}")
    except Exception as e:
        logging.error(f"Erro no relatório: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao gerar relatório.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        iniciar_sniper()
        await update.message.reply_text("🟢 Sniper iniciado com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao iniciar sniper: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao iniciar o sniper.")

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parar_sniper()
        await update.message.reply_text("🔴 Sniper parado.")
    except Exception as e:
        logging.error(f"Erro ao parar sniper: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao parar o sniper.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status()
        motivo = getattr(risk_manager, "last_block_reason", None)
        if motivo:
            status_text = f"{status}\n\n⚠️ Último motivo de bloqueio: {motivo}"
        else:
            status_text = status
        await update.message.reply_text(f"📈 Status do sniper:\n{status_text}")
    except Exception as e:
        logging.error(f"Erro no status sniper: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao obter status.")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await update.message.reply_text("🏓 Pong!")
    elapsed_ms = int((time.time() - start_time) * 1000)
    await msg.edit_text(f"🏓 Pong! ({elapsed_ms} ms)")

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="🔔 Notificação de teste enviada."
        )
        await update.message.reply_text("✅ Notificação enviada.")

except Exception as e:
        logging.error(f"Erro ao enviar notificação de teste: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Falha ao enviar notificação.")

# --- Inicialização do bot ---
def iniciar_bot():
    global application
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrar comandos no menu do Telegram
    comandos_menu = [
        BotCommand("snipe", "Inicia o sniper"),
        BotCommand("stop", "Para o sniper"),
        BotCommand("sniperstatus", "Status do sniper"),
        BotCommand("status", "Verifica saldo da carteira"),
        BotCommand("ping", "Teste de vida do bot"),
        BotCommand("testnotify", "Envia notificação de teste"),
        BotCommand("menu", "Exibe o menu de comandos"),
        BotCommand("relatorio", "Mostra relatório de operações"),
    ]
    asyncio.run(application.bot.set_my_commands(comandos_menu))

    # Adicionar handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("relatorio", relatorio_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("testnotify", test_notify_cmd))

    # Mensagem de texto genérica (se desejar futuramente tratar outros textos)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_cmd))

    # Iniciar o loop do Telegram
    Thread(target=application.run_polling, daemon=True).start()

# --- Rotas Flask ---
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook_handler():
    if application:
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.update_queue.put_nowait(update)
    return "OK"

@app.route("/setwebhook")
def set_webhook():
    if not WEBHOOK_URL:
        return "⚠️ WEBHOOK_URL não configurada."
    full_url = f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    success = application.bot.set_webhook(full_url)
    return "✅ Webhook configurado." if success else "⚠️ Falha ao configurar webhook."

@app.route("/")
def index():
    return "🤖 Bot sniper rodando com Flask + Telegram!"

# --- Main ---
if __name__ == "__main__":
    try:
        iniciar_bot()
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    except Exception as e:
        logging.error(f"Erro crítico: {e}", exc_info=True)
