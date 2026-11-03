"""Отправка в Telegram: copy_message из лички админа (форматирование сохраняется)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from app import texts
from app.config import NETWORK_TG
from app.senders.base import HealthStatus, SendResult, SourceMessage


class TelegramSender:
    network = NETWORK_TG

    def __init__(self, bot: Bot, target_chat_id: int | None) -> None:
        self._bot = bot
        self._target = target_chat_id

    async def health(self) -> HealthStatus:
        try:
            me = await self._bot.get_me()
            detail = f"@{me.username}"
            if self._target is not None:
                chat = await self._bot.get_chat(self._target)
                detail = chat.title or str(chat.id)
        except TelegramAPIError as exc:
            return HealthStatus(False, exc.message)
        return HealthStatus(True, detail)

    async def check_chat(self, chat_id: str) -> HealthStatus:
        """Видит ли бот чат. detail — название чата или текст ошибки."""
        try:
            chat = await self._bot.get_chat(chat_id)
        except TelegramAPIError as exc:
            return HealthStatus(False, exc.message)
        return HealthStatus(True, chat.title or str(chat.id))

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult:
        try:
            if source is not None:
                copied = await self._bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source.chat_id,
                    message_id=source.message_id,
                )
                return SendResult.success(str(copied.message_id))
            sent = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
            return SendResult.success(str(sent.message_id))
        except TelegramRetryAfter as exc:
            return SendResult.failure(exc.message, retryable=True, retry_after=exc.retry_after)
        except TelegramServerError as exc:
            return SendResult.failure(exc.message, retryable=True)
        except TelegramNetworkError as exc:
            # aiogram не отличает «не соединились» от «не дождались ответа» — повтор мог бы
            # продублировать пост, поэтому не повторяем.
            return SendResult.failure(f"{exc.message}; {texts.MAYBE_DELIVERED}")
        except TelegramAPIError as exc:
            return SendResult.failure(exc.message)
