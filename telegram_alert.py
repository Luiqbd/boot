import logging

logger = logging.getLogger(__name__)

class TelegramAlert:
    def __init__(self, bot, chat_id):
        self.bot = bot
        self.chat_id = chat_id

    def send(self, message: str):
        try:
            self.bot.send_message(chat_id=self.chat_id, text=message)
            logger.info(f"Alerta enviado: {message}")
        except Exception as e:
            logger.error(f"Erro ao enviar alerta: {e}")
