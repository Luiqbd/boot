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
import uuid
from web3 import Web3

# --- Importações sniper ---
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status
from config import config

# --- Trade executors ---
from trade_executor import RealTradeExecutor, SafeTradeExecutor

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

# --- Variáveis de ambiente e auxiliares ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "0")

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

# --- Handlers de Telegram (inalterados) ---
# (start_cmd, menu_cmd, status_cmd, snipe_cmd, stop_cmd, sniper_status_cmd,
#  echo, ping_cmd, test_notify_cmd, health, webhook, set_webhook_with_retry, start_flask)

def iniciar_sniper(executor):
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return

    logging.info("⚙️ Iniciando sniper... Monitorando novos pares em todas as DEX.")

    def start_sniper():
        try:
            run_discovery(
                lambda dex, pair, t0, t1: on_new_pair(
                    dex,
                    pair,
                    t0,
                    t1,
                    bot=application.bot,
                    loop=loop,
                    executor=executor
                ),
                loop
            )
        except Exception as e:
            logging.error(f"Erro no sniper: {e}", exc_info=True)

    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

# --- Inicialização principal ---
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN no ambiente. Encerrando.")
        raise SystemExit(1)
    if not WEBHOOK_URL:
        logging.warning("WEBHOOK_URL não definido. O webhook não será registrado automaticamente.")

    missing = [k for k in ["RPC_URL", "PRIVATE_KEY", "CHAIN_ID"] if not os.getenv(k)]
    if missing:
        logging.error(f"Faltam variáveis de ambiente obrigatórias: {', '.join(missing)}. Encerrando.")
        raise SystemExit(1)

    # Valida chave e loga a carteira ativa
    try:
        addr = get_active_address()
        logging.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Falha ao validar PRIVATE_KEY: {e}", exc_info=True)
        raise SystemExit(1)

    # Define dry_run e instancia executor
    dry_run = str_to_bool(os.getenv("DRY_RUN", "true"))
    logging.info(f"🔄 Modo dry_run: {dry_run}")

    w3 = Web3(Web3.HTTPProvider(os.getenv("RPC_URL")))
    wallet_address = get_active_address()
    trade_size = float(os.getenv("TRADE_SIZE_ETH", "0.01"))
    slippage_bps = int(os.getenv("SLIPPAGE_BPS", "50"))

    executor = (
        SafeTradeExecutor(w3, wallet_address, trade_size, slippage_bps)
        if dry_run
        else RealTradeExecutor(w3, wallet_address, trade_size, slippage_bps)
    )

    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registra handlers (igual ao que você já tinha)
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("menu", menu_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("snipe", lambda u, c: iniciar_sniper(executor)))
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
            BotCommand("status", "Mostra saldo ETH/WETH da carteira"),
            BotCommand("snipe", "Inicia o sniper"),
            BotCommand("stop", "Para o sniper"),
            BotCommand("sniperstatus", "Status do sniper"),
            BotCommand("ping", "Teste de vida (pong)"),
            BotCommand("testnotify", "Envia uma notificação de teste")
        ])

        # Log informativo: DEX monitoradas
        try:
            dex_lines = [
                f"- {d['name']} | type={d['type']} | factory={d['factory']} | router={d['router']}"
                for d in config.get("DEXES", [])
            ]
            if dex_lines:
                logging.info("🔎 DEX monitoradas:\n" + "\n".join(dex_lines))
        except Exception as e:
            logging.warning(f"Não foi possível listar DEXES no startup: {e}")

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
