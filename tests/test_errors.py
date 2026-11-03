from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage, SetMyCommands

from app import texts
from app.bot.keyboards import DelayCb
from app.bot.setup import build_dispatcher, setup_bot_commands
from app.senders.base import SenderError
from app.senders.max import MaxSender
from app.senders.wa_evolution import EvolutionSender
from app.senders.wa_greenapi import GreenApiSender
from app.storage import Storage
from tests.helpers import ADMIN_ID, MockedBot, callback_update, command_update, message_update
from tests.test_handlers import FakeQueue, make_settings

EVO = "http://evolution-api:8080"
MAX = "https://platform-api2.max.ru"
GROUP_ID = -100500  # chat id группы в tests.helpers.message_update


class BrokenRuntime:
    """Любое обращение к настройкам падает; в тексте ошибки — токен бота (42:TEST)."""

    def __getattr__(self, name: str) -> Any:
        async def boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom 42:TEST")

        return boom


async def broken_env(storage: Storage) -> tuple[MockedBot, Any]:
    async def titles() -> dict[str, str]:
        return {}

    dp = build_dispatcher(
        make_settings(), db=storage, queue=FakeQueue(), runtime=BrokenRuntime(), wa_titles=titles
    )
    return MockedBot(), dp


# --- глобальный обработчик ошибок ---


async def test_error_in_message_handler_reported_to_admin(storage: Storage) -> None:
    bot, dp = await broken_env(storage)
    await dp.feed_update(bot, message_update(text="Пост"))  # приём поста читает группы
    (sent,) = bot.sent("SendMessage")
    assert sent.chat_id == ADMIN_ID
    assert sent.text == texts.ERROR_MESSAGE.format(reason="RuntimeError: boom ***")
    assert "42:TEST" not in sent.text  # токен замаскирован
    await bot.session.close()


async def test_error_in_callback_answers_button(storage: Storage) -> None:
    bot, dp = await broken_env(storage)
    await dp.feed_update(bot, callback_update(DelayCb(key="5-30m").pack(), 5))
    (answer,) = bot.sent("AnswerCallbackQuery")
    assert answer.text == texts.ERROR_ALERT and answer.show_alert
    (sent,) = bot.sent("SendMessage")
    assert sent.chat_id == ADMIN_ID and "RuntimeError" in sent.text
    await bot.session.close()


async def test_error_in_group_goes_to_private(storage: Storage) -> None:
    """Ошибка в группе (/here) — сообщение админу в личку, не в группу."""

    class GroupFailBot(MockedBot):
        async def __call__(self, method: Any, request_timeout: int | None = None) -> Any:
            if isinstance(method, SendMessage) and method.chat_id == GROUP_ID:
                self.calls.append(method)
                raise TelegramForbiddenError(method, "bot is not a member")
            return await super().__call__(method, request_timeout)

    bot = GroupFailBot()
    dp = build_dispatcher(make_settings(), db=storage, queue=FakeQueue())
    await dp.feed_update(bot, command_update("/here", chat_type="supergroup"))
    group_msg, admin_msg = bot.sent("SendMessage")
    assert group_msg.chat_id == GROUP_ID
    assert admin_msg.chat_id == ADMIN_ID and "TelegramForbiddenError" in admin_msg.text
    await bot.session.close()


# --- устаревшие кнопки ---


@pytest.mark.parametrize("data", ["zz:old", "d:unknown", "wg"])
async def test_stale_button(storage: Storage, data: str) -> None:
    bot = MockedBot()
    dp = build_dispatcher(make_settings(), db=storage, queue=FakeQueue())
    await dp.feed_update(bot, callback_update(data, 5))
    (answer,) = bot.sent("AnswerCallbackQuery")
    assert answer.text == texts.STALE_BUTTON and answer.show_alert
    assert bot.sent("SendMessage") == []
    await bot.session.close()


# --- список команд ---


async def test_bot_commands_set_for_private_chats() -> None:
    bot = MockedBot()
    await setup_bot_commands(bot)
    (call,) = [c for c in bot.calls if isinstance(c, SetMyCommands)]
    assert [c.command for c in call.commands] == [c for c, _ in texts.BOT_COMMANDS]
    assert call.scope is not None and call.scope.type == "all_private_chats"
    await bot.session.close()


async def test_bot_commands_failure_is_not_fatal() -> None:
    bot = AsyncMock()
    bot.set_my_commands.side_effect = TelegramBadRequest(AsyncMock(), "nope")
    await setup_bot_commands(bot)  # не падает


# --- ответы не в JSON ---


@pytest.fixture
async def client() -> Any:
    async with httpx.AsyncClient() as c:
        yield c


HTML = httpx.Response(200, text="<html>502 Bad Gateway</html>")


@respx.mock
async def test_evolution_non_json(client: httpx.AsyncClient) -> None:
    evo = EvolutionSender(client, EVO, "k", "main", typing=False)
    respx.get(f"{EVO}/group/fetchAllGroups/main").mock(return_value=HTML)
    with pytest.raises(SenderError, match=re.escape(texts.BAD_RESPONSE)):
        await evo.list_groups()
    respx.get(f"{EVO}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json=["not", "an", "object"])
    )
    health = await evo.health()
    assert not health.ok and texts.BAD_RESPONSE in health.detail


@respx.mock
async def test_greenapi_non_json(client: httpx.AsyncClient) -> None:
    green = GreenApiSender(client, "https://api.green-api.com", "1101", "tok", typing=False)
    respx.get("https://api.green-api.com/waInstance1101/getStateInstance/tok").mock(
        return_value=HTML
    )
    health = await green.health()
    assert not health.ok and texts.BAD_RESPONSE in health.detail


@respx.mock
async def test_max_non_json(client: httpx.AsyncClient) -> None:
    max_sender = MaxSender(client, MAX, "t")
    respx.get(f"{MAX}/me").mock(return_value=HTML)
    health = await max_sender.health()
    assert not health.ok and texts.BAD_RESPONSE in health.detail
    respx.get(f"{MAX}/updates").mock(return_value=HTML)
    with pytest.raises(SenderError):
        await max_sender.poll_chats(None, 0)
