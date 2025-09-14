# notifier.py

from telegram import Bot
from config import config

_bot = Bot(token=config["TELEGRAM_TOKEN"])
_chat_id = config["TELEGRAM_CHAT_ID"]

def send(text: str) -> None:
    """
    Envia uma mensagem de texto ao chat configurado no Telegram.
    """
    _bot.send_message(chat_id=_chat_id, text=text)
