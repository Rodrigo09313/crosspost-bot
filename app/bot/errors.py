"""Глобальный обработчик ошибок и «чужих» кнопок: админ никогда не получает тишину."""

from __future__ import annotations

import contextlib
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, ErrorEvent

from app import texts
from app.config import Settings

log = logging.getLogger(__name__)

REASON_LIMIT = 200


def short_reason(exc: BaseException, secrets: list[str]) -> str:
    """Тип и текст ошибки для админа: коротко и без секретов."""
    reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    for secret in secrets:
        reason = reason.replace(secret, "***")
    return reason[:REASON_LIMIT]


async def on_error(event: ErrorEvent, bot: Bot, settings: Settings) -> bool:
    log.error("Ошибка при обработке апдейта %s", event.update.update_id, exc_info=event.exception)
    reason = escape(short_reason(event.exception, settings.secrets()))
    update = event.update
    user_id: int | None = None
    if update.callback_query is not None:
        user_id = update.callback_query.from_user.id
        # кнопка могла устареть — сообщение ниже всё равно уйдёт
        with contextlib.suppress(TelegramAPIError):
            await update.callback_query.answer(texts.ERROR_ALERT, show_alert=True)
    elif update.message is not None and update.message.from_user is not None:
        user_id = update.message.from_user.id
    if user_id is not None:
        # В личку, даже если ошибка случилась в группе (/here): в группу ошибки не пишем.
        try:
            await bot.send_message(user_id, texts.ERROR_MESSAGE.format(reason=reason))
        except TelegramAPIError as exc:
            log.warning("Не удалось сообщить админу об ошибке: %s", exc.message)
    return True


async def stale_button(cb: CallbackQuery) -> None:
    """Кнопка, которую никто не обработал: старое сообщение или прошлая версия бота."""
    await cb.answer(texts.STALE_BUTTON, show_alert=True)
