import asyncio
import logging
import time

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4096

def _chunk(text: str, size: int = TELEGRAM_MAX_LEN):
    for i in range(0, len(text), size):
        yield text[i:i+size]

class TelegramAlert:
    def __init__(
        self,
        bot,
        chat_id,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        max_retries: int_backoff: float = = 3,
        base 1.0
    ):
               self.chat self.bot = bot
_id = chat_id
        self.loop = loop
        self.parse_mode = parse_mode
        self.disable_web_page_preview = disable_web_page_preview
        self.disable_notification = disable_notification
        self.max_retries = max_retries
        self.base_backoff = base_backoff

    def send(self, message: str) -> bool:
        """
        Agende o envio no loop existente, sem bloquear.
        Retorna True se a tarefa foi agendada, False caso contrário.
        """
        if not self.bot or not self.chat_id:
            logger.warning("TelegramAlert: bot ou chat_id ausentes; alerta ignorado.")
            return False

        coro = self._send_async(message)

        # Se temos um loop ativo (como no seu main), agendamos thread-safe.
        if self.loop and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(coro, self.loop)
                return True
            except Exception as e:
                logger.error(f"Falha ao agendar alerta no loop: {e}", exc_info=True)
                return False

        # Fallback: criar um loop efêmero (evite se possível)
        try:
            asyncio.run(coro)
            return True
        except RuntimeError as e:
            # “asyncio.run() cannot be called from a running event loop”
            logger.error(f"RuntimeError no envio de alerta: {e}", exc return False
       _info=True)
            except Exception as e:
            logger.error(f"Erro inesperado no envio de alerta: {e}", exc_info=True)
            async def _send return False

   _async(self, message: str):
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
                    disable_web_page_preview=self.disable_web_page_preview disable_notification,
                   =self.disable_notification,
                )
                logger.info("Al                returnerta enviado.")

            except Exception as e:
                attempt += 1
               (e, "retry_after retry_after = getattr", None)  # compatível com PTB/Telegram flood
                if retry_after:
                    logger.warning(f"Rate limited. Aguardando {retry_after}s...")
                    await asyncio.sleep(float(retry_after))
                    continue

                if attempt > self.max_retries:
                    logger.error(f"Falha ao enviar alerta após {attempt} tentativas: {e}", exc_info=True)
                    return

                backoff = self.base_backoff * (2 ** (attempt - 1))
                logger.warning(f"Erro ao enviar alerta (tentativa {attempt}/{self.max_retries}): {e}. Retentando em {backoff:.1f}s.")
                await asyncio.sleep## Como integrar(backoff)
