import asyncio
import logging
import random
from typing import Optional, List

try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Bot = None
    TelegramError = Exception

from utils import escape_md_v2

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN: int = 4096


class TelegramAlert:
    """
    Gerencia envio de mensagens ao Telegram com:
      - divisão em partes (chunking)
      - retries com backoff exponencial + jitter
      - suporte a loop existente ou criação de loop standalone
      - escape automático de MarkdownV2
    """

    def __init__(
        self,
        bot: Optional[Bot],
        chat_id: int,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        disable_notification: bool = False,
        max_retries: int = 3,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.loop = loop or asyncio.get_event_loop()
        self.parse_mode = parse_mode
        self.disable_web_page_preview = disable_web_page_preview
        self.disable_notification = disable_notification
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff

    @staticmethod
    def _chunk_text(text: str, size: int = TELEGRAM_MAX_LEN) -> List[str]:
        """
        Divide `text` em pedaços de no máximo `size` caracteres,
        respeitando o limite da API do Telegram.
        """
        return [text[i : i + size] for i in range(0, len(text), size)]

    def send(self, message: str) -> bool:
        """
        Envia `message` de forma thread-safe:
          - Se um loop estiver rodando, agenda com run_coroutine_threadsafe
          - Caso contrário, cria e executa um loop temporário.
        Aplica escape de MarkdownV2 e faz chunking.
        Retorna True se o envio foi agendado ou executado sem erro imediato.
        """
        if not self.bot or not self.chat_id:
            logger.warning("TelegramAlert: bot ou chat_id não configurados.")
            return False

        # escape MarkdownV2 antes do chunking
        escaped = escape_md_v2(message)
        coro = self._send_all(escaped)

        try:
            if self.loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, self.loop)
            else:
                asyncio.run(coro)
            return True

        except Exception:
            logger.error("Falha ao agendar/executar alerta", exc_info=True)
            return False

    async def _send_all(self, message: str) -> None:
        """
        Envia cada parte de `message` (já escapada), respeitando tamanho máximo,
        usando o método `_send_with_retries`.
        """
        parts = self._chunk_text(message)
        for idx, part in enumerate(parts, start=1):
            label = f"chunk {idx}/{len(parts)}"
            await self._send_with_retries(part, label)

    async def _send_with_retries(self, text: str, label: str) -> None:
        """
        Tenta enviar `text` até `max_retries` vezes, com backoff exponencial
        + jitter. Aguarda também em caso de rate limit (RetryAfter).
        """
        attempt = 0

        while attempt <= self.max_retries:
            attempt += 1
            try:
                if not self.bot or not TELEGRAM_AVAILABLE:
                    logger.warning(f"Telegram não disponível - simulando envio: {text[:50]}...")
                    return
                    
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=self.parse_mode,
                    disable_web_page_preview=self.disable_web_page_preview,
                    disable_notification=self.disable_notification,
                )
                logger.info(f"TelegramAlert enviado ({label}) [attempt={attempt}]")
                return

            except TelegramError as te:
                retry_after = getattr(te, "retry_after", None)
                if retry_after:
                    backoff = float(retry_after)
                    logger.warning(
                        f"[{label}] Rate limit: aguardando {backoff:.1f}s antes de retry"
                    )
                    await asyncio.sleep(backoff)
                    continue

                backoff = min(self.base_backoff * 2 ** (attempt - 1), self.max_backoff)
                jitter = random.uniform(0, backoff * 0.1)
                sleep_time = backoff + jitter

                if attempt <= self.max_retries:
                    logger.warning(
                        f"[{label}] Erro no envio (tent {attempt}/{self.max_retries}): "
                        f"{te}. Retentando em {sleep_time:.1f}s."
                    )
                    await asyncio.sleep(sleep_time)
                    continue

                logger.error(
                    f"[{label}] Falha definitiva ao enviar alerta após "
                    f"{self.max_retries} tentativas: {te}",
                    exc_info=True
                )
                return

            except Exception as e:
                logger.error(f"[{label}] Erro inesperado: {e}", exc_info=True)
                return


def send_report(
    bot: Bot,
    message: str,
    chat_id: Optional[int] = None,
    **alert_kwargs
) -> bool:
    """
    Envia `message` via TelegramAlert. Usa chat_id padrão de config
    se não for fornecido. Aceita parâmetros extras (parse_mode, retries etc.), 
    inclusive `loop`.
    """
    from config import config  # evita import cíclico

    target = chat_id or config.get("TELEGRAM_CHAT_ID")

    # extrai o loop, se foi passado em alert_kwargs
    loop = alert_kwargs.pop("loop", None)
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

    alert = TelegramAlert(
        bot=bot,
        chat_id=target,
        loop=loop,
        **alert_kwargs
    )
    return alert.send(message)
