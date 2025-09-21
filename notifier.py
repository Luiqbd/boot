import logging
import asyncio
from telegram import Bot
from config import config

logger = logging.getLogger(__name__)
_bot = Bot(token=config["TELEGRAM_TOKEN"])
_chat_id = config["TELEGRAM_CHAT_ID"]

def send(text: str) -> None:
    """
    Envia uma mensagem de texto ao chat configurado no Telegram,
    agendando no loop assíncrono para não bloquear threads.
    """
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
