from __future__ import annotations

from collections.abc import AsyncIterator, Collection
from dataclasses import dataclass, field
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.types import InlineKeyboardMarkup

from app import texts
from app.bot.keyboards import DraftCb, StopCb
from app.bot.setup import build_dispatcher
from app.config import Settings
from app.storage import Storage
from tests.helpers import (
    ADMIN_ID,
    STRANGER_ID,
    MockedBot,
    callback_update,
    command_update,
    message_update,
)


@dataclass
class FakeQueue:
    wakes: int = 0
    stops: list[list[int] | None] = field(default_factory=list)  # аргументы stop()
    stopped: set[int] = field(default_factory=set)
    storage: Storage | None = None  # для stop() без номеров — «все незавершённые»

    @property
    def stopped_ids(self) -> set[int]:
        return set(self.stopped)

    def wake(self) -> None:
        self.wakes += 1

    async def stop(self, ids: Collection[int] | None = None) -> list[int]:
        self.stops.append(None if ids is None else list(ids))
        if ids is None:
            assert self.storage is not None
            ids = [b.id for b in await self.storage.unfinished()]
        result = [i for i in ids if i not in self.stopped]
        self.stopped.update(result)
        return result


@dataclass
class Env:
    bot: MockedBot
    dp: Dispatcher
    storage: Storage
    queue: FakeQueue
    titles_calls: list[int] = field(default_factory=list)

    async def feed(self, update: Any) -> None:
        await self.dp.feed_update(self.bot, update)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "tg_bot_token": "42:TEST",
        "admin_ids": [ADMIN_ID],
        "tg_target_chat_id": -100123,
        "max_bot_token": "maxtoken",
        "max_target_chat_id": "777",
        "wa_group_ids": ["a@g.us", "b@g.us"],
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
async def env(storage: Storage) -> AsyncIterator[Env]:
    bot = MockedBot()
    queue = FakeQueue(storage=storage)
    calls: list[int] = []

    async def wa_titles() -> dict[str, str]:
        calls.append(1)
        return {"a@g.us": "Группа A"}

    dp = build_dispatcher(make_settings(), db=storage, queue=queue, wa_titles=wa_titles)
    yield Env(bot, dp, storage, queue, calls)
    await bot.session.close()


def last_markup(bot: MockedBot) -> InlineKeyboardMarkup:
    for call in reversed(bot.calls):
        markup = getattr(call, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            return markup
    raise AssertionError("нет клавиатуры")


def button_texts(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def cb(action: str, draft_id: int = 1, net: str = "-") -> str:
    return DraftCb(action=action, draft_id=draft_id, net=net).pack()


# --- фильтр админов ---


async def test_stranger_is_ignored_silently(env: Env) -> None:
    await env.feed(message_update(STRANGER_ID, text="Пост"))
    await env.feed(command_update("/start", STRANGER_ID))
    await env.feed(command_update("/here", STRANGER_ID, chat_type="supergroup"))
    await env.feed(message_update(STRANGER_ID, photo=[_photo()]))
    await env.feed(callback_update(cb("yes"), 1, STRANGER_ID))
    assert env.bot.calls == []
    assert await env.storage.next_unfinished() is None


async def test_admin_start(env: Env) -> None:
    await env.feed(command_update("/start"))
    assert env.bot.texts() == [texts.START]


# --- отказ на медиа и альбомы ---


def _photo() -> dict[str, Any]:
    return {"file_id": "f", "file_unique_id": "u", "width": 10, "height": 10}


@pytest.mark.parametrize(
    "fields",
    [
        {"photo": [_photo()], "caption": "подпись"},
        {"video": {"file_id": "v", "file_unique_id": "v", "width": 1, "height": 1, "duration": 1}},
        {"document": {"file_id": "d", "file_unique_id": "d"}},
        {
            "sticker": {
                "file_id": "s",
                "file_unique_id": "s",
                "type": "regular",
                "width": 1,
                "height": 1,
                "is_animated": False,
                "is_video": False,
            }
        },
        {"voice": {"file_id": "v", "file_unique_id": "v", "duration": 1}},
    ],
)
async def test_media_refused(env: Env, fields: dict[str, Any]) -> None:
    await env.feed(message_update(**fields))
    assert env.bot.texts() == [texts.ONLY_TEXT]
    assert await env.storage.next_unfinished() is None


async def test_album_refused_once(env: Env) -> None:
    for _ in range(3):
        await env.feed(message_update(photo=[_photo()], media_group_id="album1"))
    await env.feed(message_update(photo=[_photo()], media_group_id="album2"))
    assert env.bot.texts() == [texts.ONLY_TEXT, texts.ONLY_TEXT]


# --- пост, предпросмотр, подтверждение ---


async def test_preview_shows_all_configured_networks(env: Env) -> None:
    await env.feed(message_update(text="Аванс 50% 🔴 <b>не тег</b>"))
    (preview,) = env.bot.sent("SendMessage")
    assert "Предпросмотр" in preview.text
    assert "&lt;b&gt;не тег&lt;/b&gt;" in preview.text
    assert "Частей для MAX: 1" in preview.text
    assert button_texts(last_markup(env.bot)) == [
        "✅ Telegram",
        "✅ MAX",
        "✅ WhatsApp (2)",
        texts.BTN_SEND,
        texts.BTN_CANCEL,
    ]


async def test_full_flow_creates_broadcast(env: Env) -> None:
    post = message_update(text="Пост для всех")
    await env.feed(post)
    await env.feed(callback_update(cb("send"), 5))
    confirm = env.bot.sent("EditMessageText")[-1]
    assert confirm.text.startswith("Отправить в: TG 1, MAX 1, WA 2?")
    # сети параллельно; самая длинная — WA: 2 группы, пауза 10–30 с + «печатает» → 1 мин
    assert "Примерно 1 мин" in confirm.text
    await env.feed(callback_update(cb("yes"), 5))

    b = await env.storage.next_unfinished()
    assert b is not None
    assert b.status == "queued"
    assert b.text == "Пост для всех"
    assert b.networks == ["tg", "max", "wa"]
    assert b.admin_id == ADMIN_ID
    assert post.message is not None and b.source_message_id == post.message.message_id
    deliveries = await env.storage.deliveries(b.id)
    assert [(d.network, d.target_id, d.target_title) for d in deliveries] == [
        ("tg", "-100123", "Telegram"),
        ("max", "777", "MAX"),
        ("wa", "a@g.us", "Группа A"),
        ("wa", "b@g.us", "b@g.us"),
    ]
    assert env.queue.wakes == 1
    assert env.bot.sent("EditMessageText")[-1].text == texts.QUEUED.format(id=b.id)

    # повторное нажатие «Да» не создаёт вторую рассылку
    await env.feed(callback_update(cb("yes"), 5))
    assert await env.storage.count_queued() == 1


async def test_toggle_network_off(env: Env) -> None:
    await env.feed(message_update(text="Пост"))
    await env.feed(callback_update(cb("toggle", net="wa"), 5))
    assert "⬜ WhatsApp (2)" in button_texts(last_markup(env.bot))
    await env.feed(callback_update(cb("send"), 5))
    assert env.bot.sent("EditMessageText")[-1].text.startswith("Отправить в: TG 1, MAX 1?")
    await env.feed(callback_update(cb("yes"), 5))
    b = await env.storage.next_unfinished()
    assert b is not None and b.networks == ["tg", "max"]
    assert {d.network for d in await env.storage.deliveries(b.id)} == {"tg", "max"}
    assert env.titles_calls == []


async def test_nothing_selected_is_rejected(env: Env) -> None:
    await env.feed(message_update(text="Пост"))
    for net in ("tg", "max", "wa"):
        await env.feed(callback_update(cb("toggle", net=net), 5))
    await env.feed(callback_update(cb("send"), 5))
    answer = env.bot.sent("AnswerCallbackQuery")[-1]
    assert answer.text == texts.SELECT_NETWORK and answer.show_alert


async def test_cancel_and_no(env: Env) -> None:
    await env.feed(message_update(text="Пост"))
    await env.feed(callback_update(cb("send"), 5))
    await env.feed(callback_update(cb("no"), 5))
    assert "Предпросмотр" in env.bot.sent("EditMessageText")[-1].text
    await env.feed(callback_update(cb("cancel"), 5))
    assert env.bot.sent("EditMessageText")[-1].text == texts.CANCELLED
    await env.feed(callback_update(cb("yes"), 5))
    assert env.bot.sent("AnswerCallbackQuery")[-1].text == texts.DRAFT_EXPIRED
    assert await env.storage.next_unfinished() is None


async def test_long_text_split_for_max(env: Env) -> None:
    text = "\n\n".join(["А" * 2040] * 2)  # 4082 символа: ≤ 4096 для TG, > 4000 для MAX
    await env.feed(message_update(text=text))
    assert "Частей для MAX: 2" in env.bot.sent("SendMessage")[0].text
    await env.feed(callback_update(cb("yes"), 5))
    b = await env.storage.next_unfinished()
    assert b is not None
    parts = [d.part_no for d in await env.storage.deliveries(b.id) if d.network == "max"]
    assert parts == [1, 2]


async def test_unknown_command(env: Env) -> None:
    await env.feed(command_update("/foo"))
    assert env.bot.texts() == [texts.UNKNOWN_COMMAND]


async def test_here_in_group(env: Env) -> None:
    await env.feed(command_update("/here", chat_type="supergroup"))
    assert env.bot.texts() == [texts.HERE_ID.format(chat_id=-100500)]


async def test_forward_from_channel_shows_id(env: Env) -> None:
    await env.feed(
        message_update(
            text="пост канала",
            forward_origin={
                "type": "channel",
                "date": 1,
                "chat": {"id": -1009876, "type": "channel", "title": "Канал"},
                "message_id": 3,
            },
        )
    )
    assert env.bot.texts() == [texts.CHANNEL_ID.format(title="Канал", chat_id=-1009876)]
    assert env.bot.sent("SendMessage")[0].text.count("-1009876") == 1


def _last_draft(env: Env) -> int:
    return max(env.dp["drafts"]._drafts)


async def _queue_post(env: Env, text: str) -> int:
    await env.feed(message_update(text=text))
    draft = _last_draft(env)
    await env.feed(callback_update(cb("send", draft), 5))
    await env.feed(callback_update(cb("yes", draft), 5))
    b = await env.storage.recent(1)
    return b[0][0].id


# --- «Стоп»: по одной рассылке или все ---


def _inline(env: Env) -> list[str]:
    for call in reversed(env.bot.calls):
        markup = getattr(call, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            return [b.text for row in markup.inline_keyboard for b in row]
    raise AssertionError("нет инлайн-клавиатуры")


def _answer(env: Env) -> str | None:
    answer: Any = env.bot.sent("AnswerCallbackQuery")[-1]
    return answer.text  # type: ignore[no-any-return]


async def test_stop_panel_empty(env: Env) -> None:
    await env.feed(command_update("/stop"))
    await env.feed(message_update(text=texts.BTN_MENU_STOP))
    assert env.bot.texts() == [texts.stop_screen(0)] * 2
    assert env.queue.stops == []  # «Стоп» сам ничего не останавливает — открывает список


async def test_stop_one_with_confirm(env: Env) -> None:
    first = await _queue_post(env, "Пост 1")
    second = await _queue_post(env, "Пост 2")
    await env.feed(command_update("/stop"))
    assert env.bot.texts()[-1] == texts.stop_screen(2)
    assert _inline(env) == [
        texts.stop_button(first, "Пост 1", "ждёт"),
        texts.stop_button(second, "Пост 2", "ждёт"),
        texts.BTN_STOP_ALL,
        texts.BTN_REFRESH,
    ]

    await env.feed(callback_update(StopCb(action="ask", bid=first).pack(), 5))
    confirm = env.bot.texts()[-1]
    assert f"Остановить рассылку #{first}?" in confirm and "«Пост 1»" in confirm
    assert "Уже отправлено: TG 0/1 · MAX 0/1 · WA 0/2" in confirm
    assert "Ещё не отправлено: 4" in confirm
    assert _inline(env) == [texts.BTN_STOP_YES, texts.BTN_BACK]
    assert env.queue.stops == []  # без подтверждения не останавливаем

    await env.feed(callback_update(StopCb(action="yes", bid=first).pack(), 5))
    assert env.queue.stops == [[first]]
    assert _answer(env) == texts.STOP_DONE.format(id=first)
    assert env.bot.texts()[-1] == texts.stop_screen(1)  # вторая осталась в списке
    assert _inline(env) == [texts.stop_button(second, "Пост 2", "ждёт"), texts.BTN_REFRESH]


async def test_stop_all_with_confirm(env: Env) -> None:
    await _queue_post(env, "Пост 1")
    await _queue_post(env, "Пост 2")
    await env.feed(callback_update(StopCb(action="ask_all").pack(), 5))
    assert env.bot.texts()[-1] == texts.stop_all_confirm(2)
    assert _inline(env) == [texts.BTN_STOP_ALL_YES, texts.BTN_BACK]
    await env.feed(callback_update(StopCb(action="yes_all").pack(), 5))
    assert env.queue.stops == [None]
    assert _answer(env) == texts.STOP_ALL_DONE.format(n=2)
    assert env.bot.texts()[-1] == texts.stop_screen(0)


async def test_stop_from_progress_opens_new_panel(env: Env) -> None:
    bid = await _queue_post(env, "Пост 1")
    sent_before = len(env.bot.sent("SendMessage"))
    await env.feed(callback_update(StopCb(action="ask", bid=bid, new=1).pack(), 77))
    # прогресс (сообщение 77) не трогаем: подтверждение — новой панелью внизу
    assert len(env.bot.sent("SendMessage")) == sent_before + 1
    assert f"Остановить рассылку #{bid}?" in env.bot.sent("SendMessage")[-1].text
    assert all(getattr(c, "message_id", None) != 77 for c in env.bot.sent("EditMessageText"))


async def test_stop_finished_broadcast(env: Env) -> None:
    await env.feed(callback_update(StopCb(action="ask", bid=999).pack(), 5))
    assert _answer(env) == texts.STOP_GONE.format(id=999)
    assert env.bot.texts()[-1] == texts.stop_screen(0)


async def test_confirm_warns_duplicate_and_queue(env: Env) -> None:
    first = await _queue_post(env, "Калмыкия ➖ Владикавказ")
    await env.feed(message_update(text="Калмыкия ➖ Владикавказ"))
    await env.feed(callback_update(cb("send", _last_draft(env)), 5))
    confirm = env.bot.sent("EditMessageText")[-1].text
    assert "⚠️ <b>Этот пост уже рассылался:</b>" in confirm
    assert f"#{first} · сегодня" in confirm and "в очереди" in confirm
    assert "⏳ Перед ней в очереди рассылок: 1" in confirm
    assert confirm.index("⚠️") < confirm.index("Отправить в:")


async def test_confirm_without_duplicates(env: Env) -> None:
    await env.feed(message_update(text="Новый пост"))
    await env.feed(callback_update(cb("send"), 5))
    confirm = env.bot.sent("EditMessageText")[-1].text
    assert "⚠️" not in confirm and "⏳" not in confirm
