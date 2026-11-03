"""Фильтр администраторов: всё от остальных пользователей молча игнорируется.

Исключение — событие «бота добавили в чат / убрали из чата» (my_chat_member).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User

log = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: Iterable[int]) -> None:
        self._admin_ids = frozenset(admin_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.my_chat_member is not None:
            # «Бота добавили/убрали» присылает тот, кто добавил, — часто не админ бота.
            # Обработчик только запоминает чат и пишет админам.
            return await handler(event, data)
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self._admin_ids:
            log.debug("Игнорирую апдейт от не-админа: %s", user.id if user else None)
            return None
        return await handler(event, data)
