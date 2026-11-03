from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from aiogram import Dispatcher
from aiogram.types import Chat, InlineKeyboardMarkup, User

from app import texts
from app.bot.service import chunk_lines
from app.bot.setup import build_dispatcher
from app.senders.factory import Senders
from app.senders.max import MaxSender
from app.senders.telegram import TelegramSender
from app.senders.wa_evolution import EvolutionSender
from app.storage import DeliveryPlan, Storage
from tests.helpers import ADMIN_ID, STRANGER_ID, MockedBot, command_update
from tests.test_handlers import FakeQueue, make_settings

EVO = "http://evolution-api:8080"
MAX = "https://platform-api2.max.ru"
PNG = b"\x89PNG\r\n\x1a\nqr"


@dataclass
class Env:
    bot: MockedBot
    dp: Dispatcher
    storage: Storage
    queue: FakeQueue

    async def cmd(self, command: str, uid: int = ADMIN_ID) -> None:
        await self.dp.feed_update(self.bot, command_update(command, uid))


def tg_sender() -> TelegramSender:
    bot = AsyncMock()
    bot.get_me.return_value = User(id=1, is_bot=True, first_name="B", username="cargo_bot")
    bot.get_chat.return_value = Chat(id=-100123, type="supergroup", title="Грузы TG")
    return TelegramSender(bot, -100123)


async def make_env(
    storage: Storage, senders: Senders, deps: dict[str, Any] | None = None, **settings: Any
) -> Env:
    bot = MockedBot()
    queue = FakeQueue()

    async def titles() -> dict[str, str]:
        return {}

    dp = build_dispatcher(
        make_settings(**settings),
        db=storage,
        queue=queue,
        senders=senders,
        wa_titles=titles,
        **(deps or {}),
    )
    return Env(bot, dp, storage, queue)


@pytest.fixture
async def env(storage: Storage, http: httpx.AsyncClient) -> AsyncIterator[Env]:
    senders = Senders(
        tg=tg_sender(),
        max=MaxSender(http, MAX, "maxtoken"),
        wa=EvolutionSender(http, EVO, "evo-key", "main", typing=False, qr_interval=0),
    )
    e = await make_env(storage, senders)
    yield e
    await e.bot.session.close()


# --- /status ---


@respx.mock
async def test_status(env: Env) -> None:
    respx.get(f"{MAX}/me").mock(return_value=httpx.Response(200, json={"username": "max_bot"}))
    respx.get(f"{EVO}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "close"}})
    )
    bid = await env.storage.create_broadcast(
        admin_id=ADMIN_ID,
        source_chat_id=ADMIN_ID,
        source_message_id=1,
        text="t",
        networks=["tg", "wa"],
        deliveries=[DeliveryPlan("tg", "-100", "Telegram"), DeliveryPlan("wa", "a@g.us", "A")],
    )
    await env.storage.set_status(bid, "running")
    d_tg, _ = await env.storage.deliveries(bid)
    await env.storage.finish_delivery(d_tg.id, status="ok", attempts=1)
    await env.cmd("/status")
    (text,) = env.bot.texts()
    assert "Сеть | Состояние | Чатов | Пауза" in text
    assert "Telegram | ✅ доступ есть | 1 | 10–30 с" in text
    assert "MAX | ✅ подключён | 1 | 10–30 с" in text
    assert "WhatsApp | ❌ не подключён | 2 | 10–30 с" in text
    assert "⚠️ Проблемы: 1" in text
    assert "❌ WhatsApp — не подключён (close): «💬 WhatsApp» → «📱 Подключить»" in text
    assert "№ | Пост | TG | MAX | WA" in text
    assert f"{bid} | t | 1/1 ✅ | — | 0/1 ⏳" in text
    assert "evolution" not in text  # без технических подробностей провайдера


@respx.mock
async def test_status_healthy_wa(env: Env) -> None:
    respx.get(f"{MAX}/me").mock(return_value=httpx.Response(401, json={"message": "bad token"}))
    respx.get(f"{EVO}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "open"}})
    )
    await env.cmd("/status")
    (text,) = env.bot.texts()
    assert texts.NO_ACTIVE in text
    assert "WhatsApp | ✅ подключён | 2 | 10–30 с" in text
    assert "MAX | ❌ ошибка | 1 | 10–30 с" in text
    assert "❌ MAX — HTTP 401: bad token" in text


@respx.mock
async def test_status_without_wa_and_max(storage: Storage) -> None:
    env = await make_env(
        storage,
        Senders(tg=tg_sender(), max=None, wa=None),
        max_bot_token=None,
        tg_target_chat_id=None,
    )
    await env.cmd("/status")
    (text,) = env.bot.texts()
    assert texts.NO_ACTIVE in text
    assert "⚪ Telegram — чаты не выбраны" in text
    assert "⚪ MAX — не подключён" in text
    assert "⚪ WhatsApp — не настроен" in text
    await env.bot.session.close()


# --- /history ---


async def test_history(env: Env) -> None:
    await env.cmd("/history")
    assert env.bot.texts() == [texts.HISTORY_EMPTY]
    bid = await env.storage.create_broadcast(
        admin_id=ADMIN_ID,
        source_chat_id=ADMIN_ID,
        source_message_id=1,
        text="t",
        networks=["tg", "wa"],
        deliveries=[DeliveryPlan("tg", "-100", "Telegram"), DeliveryPlan("wa", "a@g.us", "A")],
    )
    d_tg, d_wa = await env.storage.deliveries(bid)
    await env.storage.finish_delivery(d_tg.id, status="ok", attempts=1)
    await env.storage.finish_delivery(d_wa.id, status="error", attempts=2, error="x")
    await env.storage.set_status(bid, "done")
    await env.cmd("/history")
    text = env.bot.texts()[-1]
    assert "Бот хранит рассылки 30 дней" in text
    assert "Дата | TG | MAX | WA" in text
    assert text.split("\n")[2].startswith("сегодня ")  # заголовок, шапка, строка
    assert text.split("\n")[2].endswith(" | 1/1 ✅ | — | 0/1 ❌")


# --- /wa_qr ---


@respx.mock
async def test_wa_qr_sends_photo(env: Env) -> None:
    respx.get(f"{EVO}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "connecting"}})
    )
    respx.get(f"{EVO}/instance/connect/main").mock(
        return_value=httpx.Response(
            200, json={"base64": "data:image/png;base64," + base64.b64encode(PNG).decode()}
        )
    )
    await env.cmd("/wa_qr")
    (photo,) = env.bot.sent("SendPhoto")
    assert photo.caption == texts.WA_QR_CAPTION
    assert photo.photo.data == PNG


@respx.mock
async def test_wa_qr_already_connected(env: Env) -> None:
    respx.get(f"{EVO}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "open"}})
    )
    await env.cmd("/wa_qr")
    assert env.bot.texts() == [texts.WA_ALREADY_CONNECTED]


@respx.mock
async def test_wa_qr_evolution_down(env: Env) -> None:
    respx.get(f"{EVO}/instance/connectionState/main").mock(side_effect=httpx.ConnectError("x"))
    await env.cmd("/wa_qr")
    assert env.bot.texts()[0].startswith("Не удалось получить QR-код:")


async def test_wa_not_configured(storage: Storage) -> None:
    env = await make_env(storage, Senders(tg=tg_sender(), max=None, wa=None))
    await env.cmd("/wa_qr")
    await env.cmd("/wa_groups")
    await env.cmd("/max_chats")
    assert env.bot.texts() == [
        texts.WA_NOT_CONFIGURED,
        texts.WA_NOT_CONFIGURED,
        texts.MAX_NOT_CONNECTED,
    ]
    await env.bot.session.close()


# --- /max_chats ---


def inline_labels(env: Env) -> list[str]:
    for call in reversed(env.bot.calls):
        markup = getattr(call, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            return [b.text for row in markup.inline_keyboard for b in row]
    raise AssertionError("нет инлайн-клавиатуры")


@respx.mock
async def test_max_chats(env: Env) -> None:
    respx.get(f"{MAX}/updates").mock(
        return_value=httpx.Response(
            200, json={"updates": [{"update_type": "bot_added", "chat_id": 777}], "marker": 1}
        )
    )
    respx.get(f"{MAX}/chats/777").mock(return_value=httpx.Response(200, json={"title": "Грузы"}))
    await env.cmd("/max_chats")
    (text,) = env.bot.texts()
    assert "Выбрано: 1 из 1" in text
    assert inline_labels(env)[0] == "✅ Грузы"  # 777 из .env — уже выбран, название обновилось


@respx.mock
async def test_max_chats_empty(env: Env) -> None:
    respx.get(f"{MAX}/updates").mock(
        return_value=httpx.Response(200, json={"updates": [], "marker": None})
    )
    respx.get(f"{MAX}/chats/777").mock(return_value=httpx.Response(404, json={}))
    await env.cmd("/max_chats")
    assert texts.MAX_CHATS_EMPTY in env.bot.texts()[-1]
    assert inline_labels(env)[0] == f"✅ MAX ({texts.NOT_IN_CHAT})"


async def test_service_commands_ignore_strangers(env: Env) -> None:
    for command in ("/status", "/history", "/wa_qr", "/wa_groups", "/max_chats", "/stop"):
        await env.cmd(command, STRANGER_ID)
    assert env.bot.calls == []


def test_chunk_lines() -> None:
    lines = [f"строка {i} " + "x" * 90 for i in range(100)]
    chunks = chunk_lines(lines, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines
