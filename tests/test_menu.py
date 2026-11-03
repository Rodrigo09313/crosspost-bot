from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app import texts
from app.bot.keyboards import DelayCb, NavCb, WaGroupCb
from app.runtime import RuntimeConfig
from app.senders.factory import Senders
from app.senders.max import MaxSender
from app.senders.wa_evolution import EvolutionSender
from app.storage import Storage
from tests.helpers import ADMIN_ID, STRANGER_ID, callback_update, message_update
from tests.test_handlers import make_settings
from tests.test_service_commands import EVO, MAX, Env, make_env, tg_sender

GROUPS_URL = f"{EVO}/group/fetchAllGroups/main"


def groups_response(*groups: tuple[str, str]) -> httpx.Response:
    return httpx.Response(200, json=[{"id": gid, "subject": title} for gid, title in groups])


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


async def press(env: Env, label: str, uid: int = ADMIN_ID) -> None:
    await env.dp.feed_update(env.bot, message_update(uid, text=label))


async def click(env: Env, data: str, uid: int = ADMIN_ID) -> None:
    await env.dp.feed_update(env.bot, callback_update(data, 5, uid))


def runtime(env: Env) -> RuntimeConfig:
    rt = env.dp["runtime"]
    assert isinstance(rt, RuntimeConfig)
    return rt


def last_inline(env: Env) -> list[str]:
    for call in reversed(env.bot.calls):
        markup = getattr(call, "reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            return [b.text for row in markup.inline_keyboard for b in row]
    raise AssertionError("нет инлайн-клавиатуры")


def last_text(env: Env) -> str:
    return env.bot.texts()[-1]


# --- настройки в SQLite ---


async def test_runtime_seed_and_persist(storage: Storage) -> None:
    settings = make_settings(wa_group_ids=["a@g.us"])
    rt = RuntimeConfig(storage, settings)
    assert await rt.wa_groups() == {"a@g.us": "a@g.us"}  # стартовое значение из .env
    assert (await rt.delay()).key == "10-30s"
    assert await rt.delay_range() == (10, 30)

    await rt.set_wa_group("b@g.us", "Юг", selected=True)
    await rt.set_wa_group("a@g.us", "", selected=False)
    await rt.set_delay("1-3m")

    again = RuntimeConfig(storage, settings)  # «перезапуск бота»
    assert await again.wa_groups() == {"b@g.us": "Юг"}
    assert (await again.delay()).key == "1-3m"

    await again.set_wa_group("b@g.us", "", selected=False)
    empty = RuntimeConfig(storage, settings)
    assert await empty.wa_groups() == {}  # сняли все — .env больше не подставляется

    with pytest.raises(ValueError):
        await rt.set_delay("1-2h")


async def test_update_titles_only_for_selected(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings(wa_group_ids=["a@g.us"]))
    await rt.update_titles({"a@g.us": "Север", "z@g.us": "Чужая"})
    assert await rt.wa_groups() == {"a@g.us": "Север"}


# --- главное меню ---

STATE_URL = f"{EVO}/instance/connectionState/main"


def wa_state(state: str) -> None:
    respx.get(STATE_URL).mock(return_value=httpx.Response(200, json={"instance": {"state": state}}))


async def test_start_and_help_show_menu(env: Env) -> None:
    await press(env, "/start")
    await press(env, texts.BTN_MENU_HELP)
    start, help_ = env.bot.sent("SendMessage")
    assert start.text == texts.START and help_.text == texts.HELP
    for call in (start, help_):
        assert isinstance(call.reply_markup, ReplyKeyboardMarkup)
        assert call.reply_markup.is_persistent
    rows = [[b.text for b in row] for row in start.reply_markup.keyboard]
    assert rows == [
        [texts.BTN_MENU_POST, texts.BTN_MENU_STOP],
        [texts.BTN_MENU_TG, texts.BTN_MENU_WA, texts.BTN_MENU_MAX],
        [texts.BTN_MENU_SETTINGS, texts.BTN_MENU_HELP],
    ]


async def test_menu_button_is_not_a_post(env: Env) -> None:
    await press(env, texts.BTN_MENU_POST)
    await press(env, texts.LEGACY_POST)  # кнопка старого меню
    assert env.bot.texts() == [texts.POST_HOWTO, texts.POST_HOWTO]
    await press(env, texts.BTN_MENU_STOP)
    assert last_text(env) == texts.stop_screen(0)
    assert await env.storage.next_unfinished() is None


async def test_stranger_menu_ignored(env: Env) -> None:
    await press(env, texts.BTN_MENU_WA, STRANGER_ID)
    await click(env, NavCb(to="status").pack(), STRANGER_ID)
    await click(env, DelayCb(key="5-30m").pack(), STRANGER_ID)
    assert env.bot.calls == []
    assert (await runtime(env).delay()).key == "10-30s"


# --- одна панель на все разделы ---


@respx.mock
async def test_sections_share_one_panel(env: Env) -> None:
    wa_state("open")
    await press(env, texts.BTN_MENU_SETTINGS)  # панель: MockedBot выдаёт id 1000
    assert last_text(env) == texts.SETTINGS
    assert last_inline(env) == [texts.BTN_STATUS, texts.BTN_DELAY, texts.BTN_HISTORY]

    # кнопки внутри панели меняют её, а не шлют новые сообщения (у колбэка message_id 5)
    await click(env, NavCb(to="delay").pack())
    await click(env, NavCb(to="settings").pack())
    await click(env, NavCb(to="history").pack())
    assert len(env.bot.sent("SendMessage")) == 1
    edits = env.bot.sent("EditMessageText")
    assert [e.message_id for e in edits] == [5, 5, 5]
    assert "Пауза между чатами" in edits[0].text
    assert edits[1].text == texts.SETTINGS
    assert edits[2].text == texts.HISTORY_EMPTY
    assert last_inline(env) == [texts.BTN_BACK]

    # новый раздел из нижнего меню — новая панель, прошлая удаляется
    await press(env, texts.BTN_MENU_WA)
    (deleted,) = env.bot.sent("DeleteMessage")
    assert (deleted.chat_id, deleted.message_id) == (ADMIN_ID, 5)
    assert "WhatsApp — подключён · 2 гр." in last_text(env)
    assert last_inline(env) == [texts.BTN_WA_GROUPS, texts.BTN_WA_CONNECT]


@respx.mock
async def test_status_in_settings(env: Env) -> None:
    wa_state("open")
    respx.get(f"{MAX}/me").mock(return_value=httpx.Response(200, json={"username": "max_bot"}))
    await click(env, NavCb(to="status").pack())
    text = last_text(env)
    assert "📊 Статус" in text and "MAX | ✅ подключён | 1 | 10–30 с" in text
    assert last_inline(env) == [texts.BTN_REFRESH, texts.BTN_BACK]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        (texts.LEGACY_STATUS, texts.STATUS_TITLE),
        (texts.LEGACY_DELAY, "Пауза между чатами"),
        (texts.LEGACY_HISTORY, texts.HISTORY_EMPTY),
    ],
)
@respx.mock
async def test_legacy_buttons_still_work(env: Env, label: str, expected: str) -> None:
    wa_state("open")
    respx.get(f"{MAX}/me").mock(return_value=httpx.Response(200, json={"username": "b"}))
    await press(env, label)
    assert expected in last_text(env)


# --- WhatsApp: подключение ---


@respx.mock
async def test_wa_connect_qr_and_back(env: Env) -> None:
    wa_state("connecting")
    respx.get(f"{EVO}/instance/connect/main").mock(
        return_value=httpx.Response(
            200, json={"base64": "data:image/png;base64," + base64.b64encode(b"qr").decode()}
        )
    )
    await click(env, NavCb(to="wa_qr").pack())
    assert last_text(env) == texts.WA_CONNECT_HOWTO
    assert last_inline(env) == [texts.BTN_WA_NEW_QR, texts.BTN_BACK]
    (photo,) = env.bot.sent("SendPhoto")
    assert photo.caption == texts.WA_QR_CAPTION

    await click(env, NavCb(to="wa").pack())  # ушли из раздела — QR убирается
    deleted = [d.message_id for d in env.bot.sent("DeleteMessage")]
    assert len(deleted) == 1 and deleted[0] >= 1000
    assert "WhatsApp — не подключён" in last_text(env)


@respx.mock
async def test_wa_connect_already_connected(env: Env) -> None:
    wa_state("open")
    await press(env, texts.LEGACY_WA_CONNECT)
    assert env.bot.texts() == [texts.WA_ALREADY_CONNECTED]
    assert env.bot.sent("SendPhoto") == []


# --- выбор групп WhatsApp ---


@respx.mock
async def test_wa_groups_picker_toggle(env: Env) -> None:
    wa_state("open")
    respx.get(GROUPS_URL).mock(
        return_value=groups_response(("a@g.us", "Юг"), ("c@g.us", "Борт <1>"))
    )
    await press(env, texts.LEGACY_WA_GROUPS)
    text = last_text(env)
    assert "Группы WhatsApp для рассылки" in text
    # b@g.us выбрана в .env, но номер в ней не состоит — видна и её можно снять
    assert "Выбрано: 2 из 3" in text
    assert last_inline(env)[:3] == [
        "✅ b@g.us (номер не в группе)",
        "⬜ Борт <1>",
        "✅ Юг",
    ]
    assert await runtime(env).wa_groups() == {"a@g.us": "Юг", "b@g.us": "b@g.us"}

    await click(env, WaGroupCb(action="toggle", gid="c@g.us").pack())
    await click(env, WaGroupCb(action="toggle", gid="b@g.us").pack())
    assert await runtime(env).wa_groups() == {"a@g.us": "Юг", "c@g.us": "Борт <1>"}
    assert "Выбрано: 2 из 2" in last_text(env)
    assert last_inline(env)[:2] == ["✅ Борт <1>", "✅ Юг"]
    assert last_inline(env)[-1] == texts.BTN_BACK

    await click(env, NavCb(to="wa").pack())
    assert "WhatsApp — подключён · 2 гр." in last_text(env)

    # предпросмотр поста берёт группы из меню
    await press(env, "Пост")
    assert "✅ WhatsApp (2)" in last_inline(env)


@respx.mock
async def test_wa_groups_picker_error_shows_selected(env: Env) -> None:
    respx.get(GROUPS_URL).mock(return_value=httpx.Response(401, json={"message": "bad key"}))
    await press(env, "/wa_groups")
    text = last_text(env)
    assert "Не удалось получить список групп" in text and "bad key" in text
    # статус «в группе» неизвестен — без пометки
    assert last_inline(env)[:2] == ["✅ a@g.us", "✅ b@g.us"]


@respx.mock
async def test_wa_groups_pagination_and_refresh(env: Env) -> None:
    route = respx.get(GROUPS_URL).mock(
        return_value=groups_response(*[(f"{i:02}@g.us", f"Группа {i:02}") for i in range(20)])
    )
    await press(env, texts.LEGACY_WA_GROUPS)
    buttons = last_inline(env)
    assert len([b for b in buttons if "Группа" in b or "@g.us" in b]) == 8
    assert "1/3" in buttons and texts.BTN_NEXT in buttons

    await click(env, WaGroupCb(action="page", page=2).pack())
    assert "3/3" in last_inline(env)

    answers_before = len(env.bot.sent("AnswerCallbackQuery"))
    await click(env, WaGroupCb(action="refresh", page=2).pack())
    assert route.call_count == 2
    # на «Обновить» отвечаем сразу и ровно один раз, до запроса к WhatsApp
    (answer,) = env.bot.sent("AnswerCallbackQuery")[answers_before:]
    assert answer.text == texts.REFRESHING


@respx.mock
async def test_wa_groups_cached(env: Env) -> None:
    """Список групп запрашивается один раз и переживает перезапуск бота."""
    route = respx.get(GROUPS_URL).mock(return_value=groups_response(("a@g.us", "Юг")))
    await press(env, texts.LEGACY_WA_GROUPS)
    # сначала панель «Загружаю…», потом на её месте — список
    assert env.bot.texts()[0] == texts.WA_GROUPS_LOADING
    assert env.bot.sent("EditMessageText")[-1].message_id == 1000
    assert "Список групп от сегодня" in last_text(env)

    await click(env, NavCb(to="wa_groups").pack())
    env.dp["wa_catalog"].__init__()  # «перезапуск»: каталог в памяти пуст
    await click(env, NavCb(to="wa_groups").pack())
    await click(env, WaGroupCb(action="page", page=0).pack())
    assert route.call_count == 1
    assert texts.WA_GROUPS_LOADING not in env.bot.texts()[1:]
    assert "✅ Юг" in last_inline(env)  # список из базы, без нового запроса

    await click(env, WaGroupCb(action="refresh").pack())  # обновить — только по кнопке
    assert route.call_count == 2
    assert texts.WA_GROUPS_LOADING in env.bot.texts()[1:]
    assert await runtime(env).wa_catalog_titles() == {"a@g.us": "Юг"}


@respx.mock
async def test_wa_groups_single_fetch(env: Env) -> None:
    """Два «Обновить» подряд, пока список грузится, — один запрос к WhatsApp."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return groups_response(("a@g.us", "Юг"))

    route = respx.get(GROUPS_URL).mock(side_effect=slow)
    catalog = env.dp["wa_catalog"]
    senders = env.dp["senders"]
    first = asyncio.create_task(catalog.refresh(senders, runtime(env)))
    await started.wait()
    second = asyncio.create_task(catalog.refresh(senders, runtime(env)))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert route.call_count == 1
    assert catalog.groups == {"a@g.us": "Юг"}


async def test_wa_groups_without_wa(storage: Storage) -> None:
    env = await make_env(storage, Senders(tg=tg_sender(), max=None, wa=None))
    await press(env, texts.LEGACY_WA_GROUPS)
    assert env.bot.texts() == [texts.WA_NOT_CONFIGURED]
    await env.bot.session.close()


# --- паузы ---


async def test_delay_menu(env: Env) -> None:
    await press(env, "/pause")
    text = last_text(env)
    assert "Сейчас: <b>10–30 с</b>" in text and "(TG 1, MAX 1, WA 2)" in text
    assert "одна на все сети" in text.lower()
    assert "✅ 10–30 с — рекомендуется" in last_inline(env)
    assert len(last_inline(env)) == 5  # 4 режима + «Назад»
    assert last_inline(env)[-1] == texts.BTN_BACK

    await click(env, DelayCb(key="1-3m").pack())
    assert (await runtime(env).delay()).key == "1-3m"
    assert "✅ 1–3 мин — очень осторожно, долго" in last_inline(env)
    answer: Any = env.bot.sent("AnswerCallbackQuery")[-1]
    assert answer.text == texts.DELAY_SET.format(label="1–3 мин")

    await click(env, DelayCb(key="5-30m").pack())  # режим удалён — старая кнопка
    assert (await runtime(env).delay()).key == "1-3m"


async def test_removed_delay_falls_back_to_default(env: Env) -> None:
    """Сохранённый режим, которого больше нет в списке, сменяется на 10–30 с."""
    await env.storage.set_setting("wa_delay", "1-10m")
    assert (await runtime(env).delay()).key == "10-30s"


async def test_pause_command(env: Env) -> None:
    await press(env, "/pause")
    assert "Пауза между чатами" in last_text(env)


async def test_fast_delays_random_in_range(env: Env) -> None:
    """Быстрые режимы: в меню есть, пауза случайная внутри диапазона."""
    await press(env, "/pause")
    assert "⬜ 5–15 с — как руками, только прогретые номера" in last_inline(env)
    await click(env, DelayCb(key="5-15s").pack())
    assert await runtime(env).delay_range() == (5, 15)
    assert "примерно 1 мин" in last_text(env)  # самая длинная очередь — 2 группы WA
    await click(env, DelayCb(key="10-30s").pack())
    assert await runtime(env).delay_range() == (10, 30)
