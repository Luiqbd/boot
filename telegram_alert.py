import asyncio
import logging
from telegram import Bot
from config import config

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4096

def _chunk(text: str, size: int = TELEGRAM_MAX_LEN):
    for i in range(0, len(text), size):
        yield text[i:i + size]


class TelegramAlert:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        max_retries: int = 3,
        base_backoff: float = 1.0
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.loop = loop
        self.parse_mode = parse_mode
        self.disable_web_page_preview = disable_web_page_preview
        self.disable_notification = disable_notification
        self.max_retries = max_retries
        self.base_backoff = base_backoff

    def send(self, message: str) -> bool:
        """
        Agenda o envio no loop existente (thread-safe) ou cria um loop novo.
        Retorna True se a tarefa foi agendada/executada com sucesso, False caso contrário.
        """
        if not self.bot or not self.chat_id:
            logger.warning("TelegramAlert: bot ou chat_id ausentes; alerta ignorado.")
            return False

        coro = self._send_async(message)

        # Se temos loop e ele está rodando, agenda thread-safe
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(coro, self.loop)
                return True
            except Exception as e:
                logger.error(f"Falha ao agendar alerta no loop: {e}", exc_info=True)
                return False

        # Caso contrário, cria um novo loop para enviar (modo standalone)
        try:
            asyncio.run(coro)
            return True
        except Exception as e:
            logger.error(f"Erro no envio de alerta: {e}", exc_info=True)
            return False

    async def _send_async(self, message: str):
        for part in _chunk(message, TELEGRAM_MAX_LEN):
            await self._send_one(part)

    async def _send_one(self, text: str):
        attempt = 0
        while True:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=self.parse_mode,
                    disable_web_page_preview=self.disable_web_page_preview,
                    disable_notification=self.disable_notification,
                )
                logger.info("Alerta enviado com sucesso.")
                return
            except Exception as e:
                attempt += 1
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    logger.warning(f"Rate limited. Aguardando {retry_after}s...")
                    await asyncio.sleep(float(retry_after))
                    continue

                if attempt > self.max_retries:
                    logger.error(f"Falha ao enviar alerta após {attempt} tentativas: {e}", exc_info=True)
                    return

                backoff = self.base_backoff * (2 ** (attempt - 1))
                logger.warning(
                    f"Erro ao enviar alerta (tentativa {attempt}/{self.max_retries}): {e}. "
                    f"Retentando em {backoff:.1f}s."
                )
                await asyncio.sleep(backoff)


def send_report(
    bot: Bot,
    message: str,
    chat_id: int | None = None
) -> bool:
    """
    Envia um relatório pelo Telegram usando TelegramAlert.
    Se chat_id não for fornecido, usa o padrão em config["TELEGRAM_CHAT_ID"].
    Retorna True se o envio foi agendado/executado com sucesso.
    """
    target_chat = chat_id or config["TELEGRAM_CHAT_ID"]
    # Tenta usar o loop corrente, se houver
    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    alert = TelegramAlert(
        bot=bot,
        chat_id=target_chat,
        loop=loop,
        parse_mode=None,
        disable_web_page_preview=True,
        disable_notification=False
    )
    return alert.send(message)
