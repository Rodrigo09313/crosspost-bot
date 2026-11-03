from __future__ import annotations

from unittest.mock import AsyncMock

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import CopyMessage
from aiogram.types import Chat, MessageId, User

from app import texts
from app.senders.base import SourceMessage
from app.senders.telegram import TelegramSender

METHOD = CopyMessage(chat_id=-100, from_chat_id=1, message_id=1)


def make_bot() -> AsyncMock:
    return AsyncMock()


async def test_copy_message() -> None:
    bot = make_bot()
    bot.copy_message.return_value = MessageId(message_id=55)
    result = await TelegramSender(bot, -100).send_text(
        "-100", "текст", source=SourceMessage(chat_id=1, message_id=10)
    )
    assert result.ok and result.message_id == "55"
    bot.copy_message.assert_awaited_once_with(chat_id="-100", from_chat_id=1, message_id=10)
    bot.send_message.assert_not_called()


async def test_send_message_without_source() -> None:
    bot = make_bot()
    bot.send_message.return_value.message_id = 7
    result = await TelegramSender(bot, -100).send_text("-100", "текст")
    assert result.ok and result.message_id == "7"


async def test_retry_after() -> None:
    bot = make_bot()
    bot.copy_message.side_effect = TelegramRetryAfter(METHOD, "Flood control", retry_after=12)
    result = await TelegramSender(bot, -100).send_text("-100", "t", source=SourceMessage(1, 1))
    assert not result.ok and result.retryable and result.retry_after == 12


async def test_network_error_not_retried() -> None:
    """Сетевая ошибка aiogram может быть и таймаутом ответа — повтор дал бы дубль."""
    bot = make_bot()
    bot.copy_message.side_effect = TelegramNetworkError(METHOD, "timeout")
    result = await TelegramSender(bot, -100).send_text("-100", "t", source=SourceMessage(1, 1))
    assert not result.ok and not result.retryable
    assert result.error is not None and texts.MAYBE_DELIVERED in result.error


async def test_permanent_errors() -> None:
    for exc in (
        TelegramBadRequest(METHOD, "chat not found"),
        TelegramForbiddenError(METHOD, "bot is not a member"),
    ):
        bot = make_bot()
        bot.copy_message.side_effect = exc
        result = await TelegramSender(bot, -100).send_text("-100", "t", source=SourceMessage(1, 1))
        assert not result.ok and not result.retryable
        assert result.error == exc.message


async def test_health() -> None:
    bot = make_bot()
    bot.get_me.return_value = User(id=1, is_bot=True, first_name="B", username="b_bot")
    bot.get_chat.return_value = Chat(id=-100, type="supergroup", title="Грузы")
    health = await TelegramSender(bot, -100).health()
    assert health.ok and health.detail == "Грузы"


async def test_health_error() -> None:
    bot = make_bot()
    bot.get_me.return_value = User(id=1, is_bot=True, first_name="B", username="b_bot")
    bot.get_chat.side_effect = TelegramBadRequest(METHOD, "chat not found")
    health = await TelegramSender(bot, -100).health()
    assert not health.ok and health.detail == "chat not found"
