from dotenv import load_dotenv
load_dotenv()  # carrega variáveis de .env ou do ambiente

import os
import asyncio
import logging
import requests
import time
import datetime
import uuid
from threading import Thread
from decimal import Decimal
from functools import wraps

from flask import Flask, request, jsonify, abort
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from web3 import Web3

from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import run_discovery, stop_discovery, get_discovery_status, DexInfo
from risk_manager import RiskManager
from config import config

# configuração de logging
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# Flask app
app = Flask(__name__)

# Decorator básico de autenticação
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        return f(*args, **kwargs)
    return decorated

# Endpoint para emissão de token via Auth0
@app.route("/api/token", methods=["GET"])
def get_token():
    client_id     = os.getenv("CLIENT_ID", "").strip()
    client_secret = os.getenv("CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return jsonify({"error": "Credenciais não configuradas"}), 500

    try:
        token = gerar_meu_token_externo(client_id, client_secret)
    except Exception as e:
        logging.error(f"Erro ao gerar token: {e}", exc_info=True)
        return jsonify({"error": "Falha ao gerar token"}), 502

    return jsonify({"token": token}), 200

# Endpoint protegido que aciona a compra
@app.route("/api/comprar", methods=["POST"])
@require_auth
def comprar():
    payload = request.get_json(silent=True) or {}
    par = payload.get("par")
    return jsonify({"status": "comprando", "par": par}), 200

# Webhook para o Telegram
@app.route("/webhook", methods=["POST"])
def webhook():
    if application is None:
        return "not ready", 503

    data = request.get_json(silent=True)
    if not data:
        app.logger.warning("Webhook: payload vazio ou não-JSON")
        return "no data", 400

    if "message" in data:
        try:
            update = Update.de_json(data, application.bot)
            asyncio.run_coroutine_threadsafe(
                application.process_update(update),
                loop
            )
            return "ok", 200
        except Exception as e:
            app.logger.error(f"Erro ao processar webhook: {e}", exc_info=True)
            return "error", 500

    return "ignored", 200

def set_webhook_with_retry(max_attempts: int = 5, delay: int = 3):
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
    WEBHOOK_URL    = os.getenv("WEBHOOK_URL", "").strip()
    if not TELEGRAM_TOKEN or not WEBHOOK_URL:
        logging.error("Faltam TELEGRAM_TOKEN ou WEBHOOK_URL.")
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
    port = int(os.getenv("PORT", "10000"))
    # Removido o caractere extra que causava SyntaxError
    app.run(host="0.0.0.0", port=port, threaded=True)

# Estado global e variáveis de ambiente
loop            = asyncio.new_event_loop()
application     = None
sniper_thread   = None

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

risk_manager = RiskManager()

# Função para obter token internamente
def fetch_token() -> str | None:
    client_id     = os.getenv("CLIENT_ID", "").strip()
    client_secret = os.getenv("CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        logging.error("CLIENT_ID ou CLIENT_SECRET não configurados.")
        return None

    try:
        return gerar_meu_token_externo(client_id, client_secret)
    except Exception as e:
        logging.error(f"Erro ao obter token interno: {e}", exc_info=True)
        return None

# Configuração de DEXes e tokens-base
dexes = [
    DexInfo(name=d.name, factory=d.factory, router=d.router, type=d.type)
    for d in config.get("DEXES", [])
]
base_tokens  = config.get("BASE_TOKENS", [config.get("WETH")])
MIN_LIQ_WETH = Decimal(str(config.get("MIN_LIQ_WETH", "0.5")))
INTERVAL_SEC = int(config.get("INTERVAL", 3))

def str_to_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "t", "yes", "y"}

def normalize_private_key(pk: str) -> str:
    if not pk:
        raise ValueError("PRIVATE_KEY não definida no ambiente.")
    pk = pk.strip()
    if pk.startswith("0x"):
        pk = pk[2:]
    if len(pk) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk):
        raise ValueError("PRIVATE_KEY inválida.")
    return pk

def get_active_address() -> str:
    pk_raw = os.getenv("PRIVATE_KEY", "")
    pk     = normalize_private_key(pk_raw)
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
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')}%\n"
        f"💧 Min. Liquidez WETH: {os.getenv('MIN_LIQ_WETH')}\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')}s\n"
        f"🧪 Dry Run: {os.getenv('DRY_RUN')}"
    )

# Inicia a thread do Sniper
def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return

    logging.info("⚙️ Iniciando sniper...")

    def start_sniper():
        token = fetch_token()
        if not token:
            logging.error("❌ Token não obtido, abortando sniper.")
            return

        try:
            loop.call_soon_threadsafe(
                run_discovery,
                Web3(Web3.HTTPProvider(config["RPC_URL"])),
                dexes,
                base_tokens,
                MIN_LIQ_WETH,
                INTERVAL_SEC,
                application.bot,
                lambda pair: on_new_pair(
                    pair.dex,
                    pair.address,
                    pair.token0,
                    pair.token1,
                    bot=application.bot,
                    loop=loop,
                    token=token
                )
            )
        except Exception as e:
            logging.error(f"Erro ao iniciar discovery: {e}", exc_info=True)

    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()

def parar_sniper():
    stop_discovery()

# Handlers do Telegram (sem alterações)

# Startup
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN no ambiente. Encerrando.")
        raise SystemExit(1)

    missing = [k for k in ("RPC_URL", "PRIVATE_KEY", "CHAIN_ID") if not os.getenv(k)]
    if missing:
        logging.error(f"Faltam variáveis obrigatórias: {', '.join(missing)}.")
        raise SystemExit(1)

    try:
        addr = get_active_address()
        logging.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Falha ao validar PRIVATE_KEY: {e}", exc_info=True)
        raise SystemExit(1)

    asyncio.set_event_loop(loop)
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrando handlers...
    # [mantém os CommandHandler e MessageHandler como antes]

    async def start_bot():
        application.bot_data["start_time"] = time.time()
        await application.initialize()
        await application.start()
        await application.bot.set_my_commands([
            BotCommand("start",        "🎯 Boas-vindas e configuração"),
            BotCommand("menu",         "📜 Reexibir o menu"),
            BotCommand("status",       "💰 Saldo ETH/WETH"),
            BotCommand("snipe",        "🟢 Iniciar sniper"),
            BotCommand("stop",         "🔴 Parar sniper"),
            BotCommand("sniperstatus", "📈 Status do sniper"),
            BotCommand("ping",         "🏓 Teste de vida"),
            BotCommand("testnotify",   "🛰️ Notificação de teste"),
            BotCommand("relatorio",    "📊 Relatório de eventos")
        ])

    loop.create_task(start_bot())
    Thread(target=start_flask,            daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
