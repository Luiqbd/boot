import os
import asyncio
import logging
import requests
import time
import datetime
import uuid
from threading import Thread
from decimal import Decimal

from flask import Flask, request
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from web3 import Web3

from check_balance import get_wallet_status
from strategy_sniper import on_new_pair
from discovery import (
    run_discovery,
    stop_discovery,
    get_discovery_status,
    DexInfo
)
from risk_manager import RiskManager
from config import config

# Configuração de logging
logging.basicConfig(
    format='[%(asctime)s] %(levelname)s - %(message)s',
    level=logging.INFO
)

# Flask app
app = Flask(__name__)

# Loop e estado global
loop = asyncio.new_event_loop()
application = None
sniper_thread = None

# Variáveis de ambiente
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
WEBHOOK_URL      = os.getenv("WEBHOOK_URL", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Gerenciador de risco para comandos HTTP/Telegram
risk_manager = RiskManager()

# --- Monta lista de DEXes e tokens-base a partir do config ---
dexes = [
    DexInfo(
        name=d.name,
        factory=d.factory,
        router=d.router,
        type=d.type
    )
    for d in config.get("DEXES", [])
]
base_tokens = config.get("BASE_TOKENS", [config.get("WETH")])

# Parâmetros de discovery
MIN_LIQ_WETH = Decimal(str(config.get("MIN_LIQ_WETH", "0.5")))
INTERVAL_SEC = int(config.get("INTERVAL", 3))


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
    pk_raw = os.getenv("PRIVATE_KEY", "")
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
        f"🏆 Take Profit: {os.getenv('TAKE_PROFIT_PCT')}%\n"
        f"💧 Min. Liquidez WETH: {os.getenv('MIN_LIQ_WETH')}\n"
        f"⏱ Intervalo: {os.getenv('INTERVAL')}s\n"
        f"🧪 Dry Run: {os.getenv('DRY_RUN')}"
    )


def iniciar_sniper():
    global sniper_thread
    if sniper_thread and sniper_thread.is_alive():
        logging.info("⚠️ O sniper já está rodando.")
        return

    logging.info("⚙️ Iniciando sniper...")

    def start_sniper():
        try:
            loop.call_soon_threadsafe(
                run_discovery,
                Web3(Web3.HTTPProvider(config["RPC_URL"])),
                dexes,
                base_tokens,
                MIN_LIQ_WETH,
                INTERVAL_SEC,
                application.bot,
                # callback ajustado: delega on_new_pair sem passar risk_manager
                lambda pair: on_new_pair(
                    pair.dex,
                    pair.address,
                    pair.token0,
                    pair.token1,
                    bot=application.bot,
                    loop=loop
                )
            )
        except Exception as e:
            logging.error(f"Erro ao iniciar discovery: {e}", exc_info=True)

    sniper_thread = Thread(target=start_sniper, daemon=True)
    sniper_thread.start()


def parar_sniper():
    stop_discovery()


# --- Handlers Telegram ---

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
        "📊 /relatorio — Gera relatório de eventos.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛠 **Configuração Atual**\n"
        f"{env_summary_text()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
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
    await update.message.reply_text("⚙️ Iniciando sniper...")
    iniciar_sniper()


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parar_sniper()
    await update.message.reply_text("🛑 Sniper interrompido.")


async def sniper_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ativo = get_discovery_status()
        text = "🟢 Sniper ativo" if ativo else "🔴 Sniper parado"
        await update.message.reply_text(text)
    except Exception as e:
        logging.error(f"Erro no /sniperstatus: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao verificar o status do sniper.")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Você disse: {update.message.text}")


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = int(time.time() - context.bot_data.get("start_time", time.time()))
    uptime_str = str(datetime.timedelta(seconds=uptime))
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await update.message.reply_text(f"pong 🏓\n⏱ Uptime: {uptime_str}\n🕒 Agora: {now_str}")


async def test_notify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0
        if chat_id == 0:
            await update.message.reply_text("⚠️ TELEGRAM_CHAT_ID inválido.")
            return

        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = str(uuid.uuid4())[:8]
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ Teste de notificação\n"
                f"🕒 {ts}\n"
                f"🆔 {uid}\n"
                "💬 Sniper pronto para operar!"
            )
        )
        await update.message.reply_text(f"Mensagem enviada (ID: {uid})")
    except Exception as e:
        logging.error(f"Erro no /testnotify: {e}", exc_info=True)
        await update.message.reply_text(f"⚠️ Erro ao enviar mensagem: {e}")

# Definição do handler /relatorio para evitar NameError
async def relatorio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # TODO: implementar lógica real de geração de relatório de eventos
        await update.message.reply_text("📊 Relatório de eventos não implementado.")
    except Exception as e:
        logging.error(f"Erro no /relatorio: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Erro ao gerar relatório.")

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


def set_webhook_with_retry(max_attempts=5, delay=3):
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
            logging.warning(f"Tentativa {attempt} levantou exceção: {e}")
        time.sleep(delay)

    logging.error("❌ Falha ao registrar webhook após várias tentativas.")


def start_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        logging.error("Falta TELEGRAM_TOKEN no ambiente. Encerrando.")
        raise SystemExit(1)

    if not WEBHOOK_URL:
        logging.warning(
            "WEBHOOK_URL não definido; webhook não será registrado automaticamente."
        )

    missing = [k for k in ("RPC_URL", "PRIVATE_KEY", "CHAIN_ID") if not os.getenv(k)]
    if missing:
        logging.error(
            f"Faltam variáveis obrigatórias: {', '.join(missing)}. Encerrando."
        )
        raise SystemExit(1)

    try:
        addr = get_active_address()
        logging.info(f"🔑 Carteira ativa: {addr}")
    except Exception as e:
        logging.error(f"Falha ao validar PRIVATE_KEY: {e}", exc_info=True)
        raise SystemExit(1)

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
    application.add_handler(CommandHandler("relatorio", relatorio_cmd))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, echo)
    )

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
            BotCommand("ping", "Teste de vida (pong)"),
            BotCommand("testnotify", "Envia notificação de teste"),
            BotCommand("relatorio", "Mostra relatório de eventos")
        ])

    loop.create_task(start_bot())
    Thread(target=start_flask, daemon=True).start()
    Thread(target=set_webhook_with_retry, daemon=True).start()

    logging.info("🚀 Bot e servidor Flask iniciados")
    loop.run_forever()
