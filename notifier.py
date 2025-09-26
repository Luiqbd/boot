import logging
import asyncio
try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Bot = None

from config import config

logger = logging.getLogger(__name__)

if TELEGRAM_AVAILABLE and config.get("TELEGRAM_TOKEN"):
    _bot = Bot(token=config["TELEGRAM_TOKEN"])
    _chat_id = config["TELEGRAM_CHAT_ID"]
else:
    _bot = None
    _chat_id = None

def send(text: str) -> None:
    """
    Envia uma mensagem de texto ao chat configurado no Telegram,
    agendando no loop assíncrono para não bloquear threads.
    """
    if not _bot or not TELEGRAM_AVAILABLE:
        logger.warning(f"Telegram não disponível - simulando envio: {text[:50]}...")
        return
        
    try:
        asyncio.get_event_loop().call_soon_threadsafe(
            asyncio.create_task,
            _bot.send_message(chat_id=_chat_id, text=text)
        )
    except Exception:
        # fallback síncrono
        try:
            _bot.send_message(chat_id=_chat_id, text=text)
        except Exception as e:
            logger.error("Falha ao notificar: %s", e, exc_info=True)
