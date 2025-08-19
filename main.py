import os
import sys
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
import uuid
from web3 import Web3

# --- Importações sniper ---
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status
from config import config
from exchange_client import ExchangeClient       # ajustado para o arquivo existente
from trade_executor import TradeExecutor         # ajustado para o arquivo existente

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
trade_executor = None   # executor será instanciado no __main__

# --- Variáveis de ambiente ---
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL      = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

# --- Funções auxiliares ---
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

# --- Funções sniper (multi-DEX com única chamada) ---
def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return

    logging.info("⚙️ Iniciando sniper... Monitorando novos pares com liquidez em todas as DEX configuradas.")

    def start_sniper():
        try:
            run_discovery(
                lambda dex, pair, t0, t1: on_new_pair(
                    dex,
                    pair,
                    t0,
                    t1,
                    bot=application.bot,
                    executor=trade_executor
                ),
                loop
            )
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery(loop)

# --- Handlers principais ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensagem = (
        "🎯 **Bem-vindo ao Sniper Bot Criado por Luis Fernando**\n\n"
        "📌 **Comandos disponíveis**\n"
        "🟢 /snipe — Inicia o sniper.\n"
        "🔴 /stop — Para o sniper.\n"
        "📈 /sniperstatus — Status do sniper.\n"
        "💰 /status — Mostra saldo ETH/WETH.\n"
        "🏓 /ping — Teste de vida.\n"
        "🛰️ /testnotify — Mensagem de teste.\n"
        "📜 /menu — Reexibe este menu.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛠 **Configuração Atual**\n"
        f"{env_summary_text()}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    await update.message.reply_text(mensagem, parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_cmd(update, context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        wallet_address = context.args[0] if context.args else None
        status = get_wallet_status(wallet_address)
        await update.message.reply_text(status)
    except Exception as e:
        logging.error(f"Erro no /status: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status da carteira.")

async def snipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if sniper_thread and sniper_thread.is_alive():
        await update.message.reply_text("⚠️ O sniper já está rodando.")
        return
    await update.message.reply_text("⚙️ Iniciando sniper... Monitorando novos pares.")
    iniciar_sniper()

async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")

async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status = get_discovery_status() or {"text": "Status indisponível."}
        await update.message.reply_text(status["text"])
    except Exception as e:
        logging.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status do sniper.")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime_seconds = int(time.time() - context.bot_data.get("start_time", time.time()))
    uptime_str     = str(datetime.timedelta(seconds=uptime_seconds))
    now_str        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(
        f"pong 🏓\n"
        f"⏱ Uptime: {uptime_str}\n"
        f"🕒 Agora: {now_str}"
    )

async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id_str = TELEGRAM_CHAT_ID or "0"
        chat_id     = int(chat_id_str) if chat_id_str.isdigit() else 0
        if chat_id == 0:
            await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID ausente ou inválido.")
            return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        unique_id = str(uuid.uuid4())[:8]

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ Teste de notificação\n"
                f"🕒 {timestamp}\n"
                f"🆔 {unique_id}\n"
                f"💬 Sniper pronto!"
            )
        )
        await update.message.reply_text(f"Mensagem de teste enviada (ID: {unique_id})")
    except Exception as e:
        logging.error(f"Erro no /testnotify: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Erro ao enviar mensagem: {e}")

# --- Healthcheck ---
@app.route("/", methods=["GET", "HEAD"])
def health():
    return "ok", 200

# --- Webhook ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if application is None:
            return 'not ready', 503
        data   = request.get_json(force=True)
        update = Update.de_json(data, application.bot)
        asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
        return 'ok', 200
    except Exception as e:
        app.logger.error(f"Erro no webhook: {e}", exc_info=True)
        return 'error', 500

def set_webhook_with_retry(max_attempts=5, delay=3):
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logging.error("WEBHOOK não configurado: faltam TELEGRAM_TOKEN ou WEBHOOK_URL.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, json={"url": WEBHOOK_URL}, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                logging.info(f"✅ Webhook registrado: {WEBHOOK_URL}")
                return
            logging.warning(f"Tentativa {attempt} falhou: {resp.text}")
        except Exception as e:
            logging.warning(f"Tentativa {attempt} erro: {e}")
        time.sleep(delay)
    logging.error("❌ Falha ao registrar webhook após várias tentativas.")

def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)

# --- Inicialização ---
if __name__ == "__main__":
    # checa variáveis essenciais
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN no ambiente. Encerrando.")
        raise SystemExit(1)
    if not WEBHOOK_URL:
        logging.warning("WEBHOOK_URL não definido. Skip webhook setup.")

    missing = [k for k in ["RPC_URL", "PRIVATE_KEY", "CHAIN_ID"] if not os.getenv(k)]
    if missing:
        logging.error(f"Faltam vars obrigatórias: {', '.join(missing)}. Encerrando.")
        raise SystemExit(1)

    # valida chave privada
    try:
        addr = get_active_address()
        logging.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Falha ao validar PRIVATE_KEY: {e}", exc_info=True)
        raise SystemExit(1)

    # instancia cliente e executor
    client = ExchangeClient(
        rpc_url=os.getenv("RPC_URL"),
        chain_id=int(os.getenv("CHAIN_ID"))
    )
    trade_executor = TradeExecutor(
        exchange_client=client,
        dry_run=str_to_bool(os.getenv("DRY_RUN", "true")),
        telegram_token=TELEGRAM_TOKEN,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )

    asyncio.set_event_loop(loop)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", snipe_cmd))
    application.add_handler(CommandHandler("stop", stop_cmd))
    application.add_handler(CommandHandler("sniperstatus", sniper_status_cmd))
    application.add_handler(CommandHandler("ping", ping_cmd))
    application.add_handler(CommandHandler("testnotify", test_notify_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    async def start_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand("start", "Mostra boas-vindas e configuração"),
            BotCommand("menu", "Reexibe o menu"),
            BotCommand("status", "Mostra saldo ETH/WETH"),
            BotCommand("snipe", "Inicia o sniper"),
            BotCommand("stop", "Para o sniper"),
            BotCommand("sniperstatus", "Status do sniper"),
            BotCommand("ping", "Teste de vida"),
            BotCommand("testnotify", "Envia uma notificação de teste")
        ])

        try:
            dex_lines = [
                f"- {d['name']} | type={d['type']} | factory={d['factory']} | router={d['router']}"
                for d in config.get("DEXES", [])
            ]
            if dex_lines:
                logging.info("🔎 DEX monitoradas:\n" + "\n".join(dex_lines))
        except Exception as e:
            logging.warning(f"Erro listando DEXES: {e}")

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
