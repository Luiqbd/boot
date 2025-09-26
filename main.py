# main.py

import os
import sys
import signal
import logging
import asyncio
import time
import datetime
import uuid
import argparse
from threading import Thread
from functools import wraps

from flask import Flask, request, jsonify, abort
try:
    from telegram import (
        Update, BotCommand,
        InlineKeyboardButton, InlineKeyboardMarkup
    )
    from telegram.ext import (
        ApplicationBuilder, CommandHandler,
        CallbackQueryHandler, MessageHandler,
        ContextTypes, filters
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    # Mock classes
    Update = None
    BotCommand = None
    InlineKeyboardButton = None
    InlineKeyboardMarkup = None
    ApplicationBuilder = None
    CommandHandler = None
    CallbackQueryHandler = None
    MessageHandler = None
    filters = None
    
    # Mock ContextTypes
    class MockContextTypes:
        DEFAULT_TYPE = None
    ContextTypes = MockContextTypes()
try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    Web3 = None

from config import config
from utils import escape_md_v2
from discovery import subscribe_new_pairs, stop_discovery, is_discovery_running
from pipeline import on_pair
from exit_manager import check_exits
from token_service import gerar_meu_token_externo
from check_balance import get_wallet_status
from risk_manager import risk_manager
from metrics import init_metrics_server
from advanced_strategy import AdvancedSniperStrategy

# Métricas Prometheus
init_metrics_server(8000)

RPC_URL     = config["RPC_URL"]
TELE_TOKEN  = config["TELEGRAM_TOKEN"]
WEBHOOK_URL = config.get("WEBHOOK_URL", "")
PORT        = int(os.getenv("PORT", 10000))

# Logger
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Web3
if WEB3_AVAILABLE:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error("RPC inacessível")
        # sys.exit(1)  # Não sair, apenas avisar
        w3 = None
else:
    w3 = None
    logger.warning("Web3 não disponível - funcionalidades blockchain limitadas")

# Advanced Strategy
try:
    advanced_sniper = AdvancedSniperStrategy()
    logger.info("✅ AdvancedSniperStrategy inicializada")
except Exception as e:
    logger.error(f"❌ Erro ao inicializar AdvancedSniperStrategy: {e}")
    advanced_sniper = None

# Telegram Bot
if TELEGRAM_AVAILABLE and TELE_TOKEN:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app_bot = ApplicationBuilder().token(TELE_TOKEN).build()
    bot = app_bot.bot
    app_bot.bot_data["start_time"] = time.time()
else:
    app_bot = None
    bot = None
    logger.warning("Telegram não disponível - bot não inicializado")

def build_menu():
    kb = [
        [InlineKeyboardButton("▶ Iniciar Sniper", "menu_snipe"),
         InlineKeyboardButton("⏹ Parar Sniper",   "menu_stop")],
        [InlineKeyboardButton("📊 Status",       "menu_status"),
         InlineKeyboardButton("💰 Saldo",        "menu_balance")],
        [InlineKeyboardButton("⚙️ Configurações", "menu_config"),
         InlineKeyboardButton("📈 Performance",   "menu_performance")],
        [InlineKeyboardButton("🎯 Posições Ativas", "menu_positions"),
         InlineKeyboardButton("📋 Histórico",     "menu_history")],
        [InlineKeyboardButton("🔍 Análise Token", "menu_analyze"),
         InlineKeyboardButton("⚡ Modo Turbo",    "menu_turbo")],
        [InlineKeyboardButton("🚫 Blacklist",    "menu_blacklist"),
         InlineKeyboardButton("✅ Whitelist",     "menu_whitelist")],
        [InlineKeyboardButton("🏓 Ping",         "menu_ping"),
         InlineKeyboardButton("🔔 TesteNotif",   "menu_testnotify")],
        [InlineKeyboardButton("📑 Relatório",    "menu_report"),
         InlineKeyboardButton("🆘 Ajuda",        "menu_help")]
    ]
    return InlineKeyboardMarkup(kb)

def build_config_menu():
    kb = [
        [InlineKeyboardButton("💵 Trade Size", "config_trade_size"),
         InlineKeyboardButton("📊 Take Profit", "config_take_profit")],
        [InlineKeyboardButton("🛡️ Stop Loss", "config_stop_loss"),
         InlineKeyboardButton("📈 Trailing Stop", "config_trailing")],
        [InlineKeyboardButton("💧 Min Liquidez", "config_liquidity"),
         InlineKeyboardButton("🏷️ Max Taxa", "config_max_tax")],
        [InlineKeyboardButton("🎯 Max Posições", "config_max_positions"),
         InlineKeyboardButton("⚡ Modo Agressivo", "config_aggressive")],
        [InlineKeyboardButton("🔙 Voltar", "menu_main")]
    ]
    return InlineKeyboardMarkup(kb)

def build_analysis_menu():
    kb = [
        [InlineKeyboardButton("📊 RSI", "analysis_rsi"),
         InlineKeyboardButton("📈 Volume", "analysis_volume")],
        [InlineKeyboardButton("💧 Liquidez", "analysis_liquidity"),
         InlineKeyboardButton("🎯 Momentum", "analysis_momentum")],
        [InlineKeyboardButton("👥 Holders", "analysis_holders"),
         InlineKeyboardButton("🔍 Score Geral", "analysis_overall")],
        [InlineKeyboardButton("🔙 Voltar", "menu_main")]
    ]
    return InlineKeyboardMarkup(kb)

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_markdown_v2(
        "🎯 *Sniper Bot*\nUse os botões abaixo:",
        reply_markup=build_menu()
    )

async def menu_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    cmd = q.data
    
    # Main menu commands
    if cmd == "menu_snipe":
        token = gerar_meu_token_externo()
        if not token:
            await q.message.reply_text("❌ Auth0 falhou")
        else:
            subscribe_new_pairs(on_pair, loop)
            await q.message.reply_text("🟢 Sniper iniciado")

    elif cmd == "menu_stop":
        stop_discovery()
        await q.message.reply_text("🔴 Sniper parado")

    elif cmd == "menu_status":
        status = "🟢 Ativo" if is_discovery_running() else "🔴 Parado"
        stats = advanced_sniper.get_performance_stats()
        status_msg = (
            f"*Status:* {status}\n"
            f"*Posições Ativas:* {stats['active_positions']}/{stats['max_positions']}\n"
            f"*Total Trades:* {stats['total_trades']}\n"
            f"*Taxa de Acerto:* {stats['win_rate']:.1f}%\n"
            f"*Lucro Total:* {stats['total_profit']:.4f} ETH"
        )
        await q.message.reply_text(status_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_balance":
        await q.message.reply_text(get_wallet_status())

    elif cmd == "menu_config":
        await q.message.edit_markdown_v2(
            "⚙️ *Configurações*\nEscolha uma opção:",
            reply_markup=build_config_menu()
        )
        return

    elif cmd == "menu_performance":
        stats = advanced_sniper.get_performance_stats()
        perf_msg = (
            f"📈 *Performance do Bot*\n\n"
            f"• Total de Trades: `{stats['total_trades']}`\n"
            f"• Trades Vencedores: `{stats['winning_trades']}`\n"
            f"• Taxa de Acerto: `{stats['win_rate']:.1f}%`\n"
            f"• Lucro Total: `{stats['total_profit']:.4f}` ETH\n"
            f"• Posições Ativas: `{stats['active_positions']}`\n"
            f"• Máx. Posições: `{stats['max_positions']}`"
        )
        await q.message.reply_text(perf_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_positions":
        positions = advanced_sniper.active_positions
        if not positions:
            await q.message.reply_text("📭 Nenhuma posição ativa no momento")
        else:
            pos_msg = "🎯 *Posições Ativas:*\n\n"
            for token, pos in positions.items():
                entry_time = datetime.datetime.fromtimestamp(pos['entry_time'])
                pos_msg += (
                    f"• `{token[:10]}...`\n"
                    f"  Entrada: `{pos['entry_price']:.8f}` ETH\n"
                    f"  Valor: `{pos['amount']:.4f}` ETH\n"
                    f"  Tempo: `{entry_time.strftime('%H:%M:%S')}`\n\n"
                )
            await q.message.reply_text(pos_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_history":
        history_msg = (
            f"📋 *Histórico de Trades*\n\n"
            f"Últimos 24h:\n"
            f"• Trades: `{risk_manager.get_trade_count_24h()}`\n"
            f"• Sucessos: `{risk_manager.get_success_count_24h()}`\n"
            f"• Falhas: `{risk_manager.get_failure_count_24h()}`\n\n"
            f"Use /report para relatório completo"
        )
        await q.message.reply_text(history_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_analyze":
        await q.message.edit_markdown_v2(
            "🔍 *Análise Técnica*\nEscolha um indicador:",
            reply_markup=build_analysis_menu()
        )
        return

    elif cmd == "menu_turbo":
        turbo_msg = (
            f"⚡ *Modo Turbo*\n\n"
            f"• Análise mais rápida\n"
            f"• Menor latência\n"
            f"• Maior agressividade\n"
            f"• ⚠️ Maior risco\n\n"
            f"Status: {'🟢 Ativo' if config.get('TURBO_MODE', False) else '🔴 Inativo'}"
        )
        await q.message.reply_text(turbo_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_blacklist":
        blacklist_msg = (
            f"🚫 *Blacklist de Tokens*\n\n"
            f"Tokens bloqueados: `{len(config.get('BLACKLIST', []))}`\n"
            f"Use /blacklist <token> para adicionar\n"
            f"Use /unblacklist <token> para remover"
        )
        await q.message.reply_text(blacklist_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_whitelist":
        whitelist_msg = (
            f"✅ *Whitelist de Tokens*\n\n"
            f"Tokens aprovados: `{len(config.get('WHITELIST', []))}`\n"
            f"Use /whitelist <token> para adicionar\n"
            f"Use /unwhitelist <token> para remover"
        )
        await q.message.reply_text(whitelist_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_ping":
        up = int(time.time() - app_bot.bot_data["start_time"])
        await q.message.reply_text(f"pong 🏓\nUptime: {datetime.timedelta(seconds=up)}")

    elif cmd == "menu_testnotify":
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = uuid.uuid4().hex[:6]
        await bot.send_message(chat_id=config["TELEGRAM_CHAT_ID"], text=f"✅ Teste {ts}\nID:{uid}")
        await q.message.reply_text(f"🔔 Enviado (ID={uid})")

    elif cmd == "menu_report":
        await q.message.reply_text(risk_manager.gerar_relatorio())

    elif cmd == "menu_help":
        help_msg = (
            f"🆘 *Ajuda do Sniper Bot*\n\n"
            f"*Comandos principais:*\n"
            f"• /start - Menu principal\n"
            f"• /status - Status do bot\n"
            f"• /balance - Saldo da carteira\n"
            f"• /report - Relatório detalhado\n\n"
            f"*Funcionalidades:*\n"
            f"• Sniper automático de novos tokens\n"
            f"• Análise técnica avançada\n"
            f"• Múltiplos níveis de take profit\n"
            f"• Stop loss dinâmico\n"
            f"• Gerenciamento de risco\n\n"
            f"*Suporte:* @seu_usuario"
        )
        await q.message.reply_text(help_msg, parse_mode="MarkdownV2")

    elif cmd == "menu_main":
        # Return to main menu
        pass

    # Configuration menu commands
    elif cmd.startswith("config_"):
        await handle_config_menu(q, cmd)
        return

    # Analysis menu commands  
    elif cmd.startswith("analysis_"):
        await handle_analysis_menu(q, cmd)
        return

    # reexibe menu principal
    try:
        await q.message.edit_markdown_v2(
            "🎯 *Sniper Bot*\nUse os botões abaixo:",
            reply_markup=build_menu()
        )
    except:
        await q.message.reply_markdown_v2(
            "🎯 *Sniper Bot*\nUse os botões abaixo:",
            reply_markup=build_menu()
        )

async def handle_config_menu(q, cmd):
    """Handle configuration menu commands"""
    if cmd == "config_trade_size":
        current_size = config.get("TRADE_SIZE_ETH", 0.1)
        msg = (
            f"💵 *Trade Size Atual:* `{current_size}` ETH\n\n"
            f"Tamanho da posição por trade.\n"
            f"Recomendado: 0.05 - 0.2 ETH\n\n"
            f"Use /set_trade_size <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_take_profit":
        tp_levels = advanced_sniper.config.take_profit_levels
        msg = (
            f"📊 *Take Profit Levels:*\n\n"
            f"• Nível 1: `{tp_levels[0]*100:.0f}%`\n"
            f"• Nível 2: `{tp_levels[1]*100:.0f}%`\n"
            f"• Nível 3: `{tp_levels[2]*100:.0f}%`\n"
            f"• Nível 4: `{tp_levels[3]*100:.0f}%`\n\n"
            f"25% da posição é vendida em cada nível"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_stop_loss":
        sl_pct = advanced_sniper.config.stop_loss_pct * 100
        msg = (
            f"🛡️ *Stop Loss:* `{sl_pct:.1f}%`\n\n"
            f"Perda máxima aceita por trade.\n"
            f"Recomendado: 5% - 15%\n\n"
            f"Use /set_stop_loss <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_trailing":
        trail_pct = advanced_sniper.config.trailing_stop_pct * 100
        msg = (
            f"📈 *Trailing Stop:* `{trail_pct:.1f}%`\n\n"
            f"Stop loss que acompanha o preço.\n"
            f"Protege lucros em alta volatilidade.\n\n"
            f"Use /set_trailing <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_liquidity":
        min_liq = advanced_sniper.config.min_liquidity
        msg = (
            f"💧 *Liquidez Mínima:* `{min_liq}` ETH\n\n"
            f"Liquidez mínima para considerar um token.\n"
            f"Maior liquidez = menor slippage\n\n"
            f"Use /set_min_liquidity <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_max_tax":
        max_tax = advanced_sniper.config.max_tax_bps / 100
        msg = (
            f"🏷️ *Taxa Máxima:* `{max_tax:.1f}%`\n\n"
            f"Taxa máxima de buy/sell aceita.\n"
            f"Tokens com taxa alta são rejeitados.\n\n"
            f"Use /set_max_tax <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_max_positions":
        max_pos = advanced_sniper.config.max_positions
        msg = (
            f"🎯 *Máx. Posições:* `{max_pos}`\n\n"
            f"Número máximo de posições simultâneas.\n"
            f"Controla exposição ao risco.\n\n"
            f"Use /set_max_positions <valor> para alterar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "config_aggressive":
        aggressive = config.get("AGGRESSIVE_MODE", False)
        status = "🟢 Ativo" if aggressive else "🔴 Inativo"
        msg = (
            f"⚡ *Modo Agressivo:* {status}\n\n"
            f"• Filtros menos rigorosos\n"
            f"• Entrada mais rápida\n"
            f"• Maior potencial de lucro\n"
            f"• ⚠️ Maior risco\n\n"
            f"Use /toggle_aggressive para alternar"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")

async def handle_analysis_menu(q, cmd):
    """Handle analysis menu commands"""
    if cmd == "analysis_rsi":
        msg = (
            f"📊 *RSI (Relative Strength Index)*\n\n"
            f"Indica se um ativo está sobrecomprado ou sobrevendido.\n\n"
            f"• RSI < 30: Sobrevendido (possível compra)\n"
            f"• RSI > 70: Sobrecomprado (possível venda)\n"
            f"• RSI 30-70: Zona neutra\n\n"
            f"Configuração atual:\n"
            f"• Mín. RSI: `{advanced_sniper.config.min_rsi_oversold}`\n"
            f"• Máx. RSI: `{advanced_sniper.config.max_rsi_overbought}`"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "analysis_volume":
        msg = (
            f"📈 *Análise de Volume*\n\n"
            f"Detecta picos de volume que indicam interesse.\n\n"
            f"• Volume Spike > 2x: Interesse alto\n"
            f"• Volume Spike > 5x: Interesse muito alto\n"
            f"• Volume constante: Sem interesse\n\n"
            f"Mínimo configurado: `{advanced_sniper.config.min_volume_spike}x`"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "analysis_liquidity":
        msg = (
            f"💧 *Análise de Liquidez*\n\n"
            f"Monitora crescimento da liquidez do pool.\n\n"
            f"• Crescimento > 20%: Muito positivo\n"
            f"• Crescimento 0-20%: Positivo\n"
            f"• Decrescimento: Negativo\n\n"
            f"Liquidez mínima: `{advanced_sniper.config.min_liquidity}` ETH"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "analysis_momentum":
        msg = (
            f"🎯 *Análise de Momentum*\n\n"
            f"Calcula a força do movimento de preço.\n\n"
            f"• Momentum > 0.5: Forte alta\n"
            f"• Momentum 0-0.5: Alta moderada\n"
            f"• Momentum < 0: Baixa\n\n"
            f"Mínimo configurado: `{advanced_sniper.config.min_momentum_score}`"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "analysis_holders":
        msg = (
            f"👥 *Análise de Holders*\n\n"
            f"Verifica distribuição de tokens entre holders.\n\n"
            f"• Score > 0.8: Bem distribuído\n"
            f"• Score 0.5-0.8: Moderadamente distribuído\n"
            f"• Score < 0.5: Concentrado (risco)\n\n"
            f"Evita tokens com poucos holders grandes."
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")
    
    elif cmd == "analysis_overall":
        msg = (
            f"🔍 *Score Geral*\n\n"
            f"Combina todos os indicadores em um score único.\n\n"
            f"• Score > 0.8: Sinal muito forte\n"
            f"• Score 0.65-0.8: Sinal forte\n"
            f"• Score 0.5-0.65: Sinal neutro\n"
            f"• Score < 0.5: Sinal fraco\n\n"
            f"Mínimo para entrada: `{advanced_sniper.config.min_signal_strength.name}`"
        )
        await q.message.reply_text(msg, parse_mode="MarkdownV2")

# Registrar handlers
if TELEGRAM_AVAILABLE and app_bot:
    app_bot.add_handler(CommandHandler("start", start_cmd))
    app_bot.add_handler(CallbackQueryHandler(menu_handler))
    app_bot.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND,
                       lambda u,c: u.message.reply_text("Use /start"))
    )

    # Comandos
    loop.run_until_complete(app_bot.initialize())
    loop.run_until_complete(app_bot.start())
    loop.run_until_complete(bot.set_my_commands([BotCommand("start","Abrir menu")]))
    if WEBHOOK_URL:
        url = WEBHOOK_URL.rstrip("/") + "/webhook"
        loop.run_until_complete(bot.set_webhook(url=url))

if TELEGRAM_AVAILABLE and app_bot:
    Thread(target=loop.run_forever, daemon=True).start()
    logger.info("🤖 Bot running")
else:
    logger.info("🤖 Bot não disponível - apenas API Flask")

# Flask API
api = Flask(__name__)

@api.route("/api/token")
def api_token():
    tok = gerar_meu_token_externo()
    return jsonify({"token":tok}) if tok else ("{}",502)

@api.route("/api/status")
def api_status():
    return jsonify({"active": is_discovery_running()})

@api.route("/webhook", methods=["POST"])
def api_webhook():
    data = request.get_json(silent=True)
    if not data or not ("message" in data or "callback_query" in data):
        return "ignored",200
    upd = Update.de_json(data, bot)
    loop.call_soon_threadsafe(asyncio.create_task, app_bot.process_update(upd))
    return "ok",200

# Graceful shutdown
def shutdown(sig, frame):
    stop_discovery()
    fut = asyncio.run_coroutine_threadsafe(app_bot.shutdown(), loop)
    try: fut.result(10)
    except: pass
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM):
    signal.signal(s, shutdown)

# Entry point
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()

    if args.worker:
        subscribe_new_pairs(on_pair, loop)
        while True:
            asyncio.get_event_loop().run_until_complete(check_exits())
    else:
        api.run("0.0.0.0", PORT, threaded=True)
