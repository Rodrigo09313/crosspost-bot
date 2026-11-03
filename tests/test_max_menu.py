"""Раздел «🟣 MAX»: подключение токеном из бота, выбор чатов, рассылка в несколько чатов."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app import texts
from app.bot.drafts import available_networks, build_plans
from app.bot.keyboards import MaxChatCb, NavCb
from app.config import NETWORK_MAX
from app.max_watch import MaxChatWatcher
from app.runtime import RuntimeConfig, Targets
from app.senders.factory import Senders, max_factory
from app.senders.max import MaxSender
from app.storage import Storage
from tests.helpers import ADMIN_ID
from tests.test_handlers import make_settings
from tests.test_menu import click, last_inline, last_text, press, runtime
from tests.test_service_commands import MAX, Env, make_env, tg_sender

UPDATES = f"{MAX}/updates"


def me(token: str, username: str = "", status: int = 200) -> respx.Route:
    route = respx.get(f"{MAX}/me", headers={"Authorization": token})
    body = {"username": username} if status == 200 else {"message": "Invalid access_token"}
    return route.mock(return_value=httpx.Response(status, json=body))


@pytest.fixture
async def env(storage: Storage, http: httpx.AsyncClient) -> AsyncIterator[Env]:
    """MAX ещё не подключён: токена нет ни в .env, ни в базе."""
    settings = make_settings(max_bot_token=None, max_target_chat_id=None)
    senders = Senders(tg=tg_sender(), max=None, wa=None)
    e = await make_env(
        storage,
        senders,
        {"max_factory": max_factory(settings, http)},
        max_bot_token=None,
        max_target_chat_id=None,
    )
    yield e
    await e.bot.session.close()


def senders(env: Env) -> Senders:
    s = env.dp["senders"]
    assert isinstance(s, Senders)
    return s


# --- подключение ---


@respx.mock
async def test_connect_with_token(env: Env) -> None:
    await press(env, texts.BTN_MENU_MAX)
    assert "MAX — не подключён" in last_text(env)
    assert last_inline(env) == [texts.BTN_MAX_CHATS, texts.BTN_MAX_CONNECT]

    await click(env, NavCb(to="max_connect").pack())
    assert last_text(env) == texts.MAX_TOKEN_ASK

    me("new-token", "cargo_max")
    await press(env, "  new-token  ")
    # сообщение с токеном удалено из чата
    (deleted,) = env.bot.sent("DeleteMessage")
    assert deleted.chat_id == ADMIN_ID
    assert await runtime(env).max_token() == "new-token"
    assert senders(env).max is not None
    text = last_text(env)
    assert texts.MAX_TOKEN_OK.format(name="cargo_max") in text
    assert "Предпросмотр" not in "".join(env.bot.texts())  # токен — не пост

    # следующий текст — снова обычный пост
    await press(env, "Пост")
    assert "Предпросмотр" in last_text(env)


@respx.mock
async def test_bad_token_keeps_waiting(env: Env) -> None:
    await click(env, NavCb(to="max_connect").pack())
    me("bad", status=401)
    await press(env, "bad")
    assert "Токен не подошёл" in last_text(env) and "Invalid access_token" in last_text(env)
    assert await runtime(env).max_token() is None
    assert senders(env).max is None

    me("good", "cargo_max")
    await press(env, "good")  # ждём токен дальше — вторая попытка
    assert await runtime(env).max_token() == "good"


@respx.mock
async def test_back_cancels_waiting(env: Env) -> None:
    await click(env, NavCb(to="max_connect").pack())
    await click(env, NavCb(to="max").pack())
    await press(env, "Пост")
    assert "Предпросмотр" in last_text(env)  # обычный пост, а не токен
    assert env.bot.sent("DeleteMessage") == []  # сообщение не приняли за токен
    assert await runtime(env).max_token() is None


@respx.mock
async def test_menu_button_cancels_waiting(env: Env) -> None:
    await click(env, NavCb(to="max_connect").pack())
    await press(env, texts.BTN_MENU_SETTINGS)
    assert last_text(env) == texts.SETTINGS
    await press(env, "secret-looking text")
    assert await runtime(env).max_token() is None


async def test_chats_need_connection(env: Env) -> None:
    await click(env, NavCb(to="max_chats").pack())
    assert last_text(env) == texts.MAX_NOT_CONNECTED


# --- выбор чатов ---


@respx.mock
async def test_pick_chats(storage: Storage, http: httpx.AsyncClient) -> None:
    sender = MaxSender(http, MAX, "maxtoken")
    env = await make_env(storage, Senders(tg=tg_sender(), max=sender, wa=None))
    await runtime(env).remember_max_chats({"888": ""}, set())  # заметил фоновый процесс
    respx.get(f"{MAX}/chats/777").mock(return_value=httpx.Response(200, json={"title": "Грузы"}))
    respx.get(f"{MAX}/chats/888").mock(return_value=httpx.Response(200, json={"title": "Юг"}))

    await click(env, NavCb(to="max_chats").pack())
    # 777 — из MAX_TARGET_CHAT_ID, уже выбран; название подтянулось из MAX
    assert last_inline(env)[:2] == ["✅ Грузы", "⬜ Юг"]
    assert last_inline(env)[-1] == texts.BTN_BACK

    await click(env, MaxChatCb(action="toggle", gid="888").pack())
    assert await runtime(env).max_chats() == {"777": "Грузы", "888": "Юг"}
    assert "Выбрано: 2 из 2" in last_text(env)

    await press(env, "Пост")
    assert "✅ MAX (2)" in last_inline(env)

    await click(env, MaxChatCb(action="toggle", gid="777").pack())
    assert await runtime(env).max_chats() == {"888": "Юг"}
    await env.bot.session.close()


# --- данные ---


async def test_runtime_max_seed_and_persist(storage: Storage) -> None:
    settings = make_settings(max_bot_token="env-token", max_target_chat_id="777")
    rt = RuntimeConfig(storage, settings)
    assert await rt.max_token() == "env-token"
    assert await rt.max_chats() == {"777": "MAX"}
    await rt.set_max_token("menu-token")
    await rt.set_max_chat("888", "Юг", selected=True)
    await rt.set_max_chat("777", "", selected=False)

    again = RuntimeConfig(storage, settings)  # «перезапуск»: база важнее .env
    assert await again.max_token() == "menu-token"
    assert await again.max_chats() == {"888": "Юг"}


async def test_targets_skip_max_without_token(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings(max_bot_token=None, max_target_chat_id="777"))
    assert (await rt.targets()).max == {}


def test_plans_split_for_each_max_chat() -> None:
    text = "слово " * 1000  # длиннее лимита MAX — несколько частей
    targets = Targets(max={"1": "A", "2": "B"})
    plans = build_plans(targets, {NETWORK_MAX}, text)
    parts = len([p for p in plans if p.target_id == "1"])
    assert parts > 1
    assert [(p.target_id, p.part_no) for p in plans] == [
        *[("1", n) for n in range(1, parts + 1)],
        *[("2", n) for n in range(1, parts + 1)],
    ]
    assert available_networks(targets) == [NETWORK_MAX]


def test_live_senders_see_new_max(http: httpx.AsyncClient) -> None:
    s = Senders(tg=tg_sender(), max=None, wa=None)
    live = s.live()
    assert NETWORK_MAX not in live
    s.max = MaxSender(http, MAX, "t")
    assert live[NETWORK_MAX] is s.max
    assert set(live) == {"tg", NETWORK_MAX} and len(live) == 2


@respx.mock
async def test_watcher_remembers_chats(storage: Storage, http: httpx.AsyncClient) -> None:
    """Фоновый процесс забирает события MAX сразу — экран «Чаты» берёт список из базы."""
    rt = RuntimeConfig(storage, make_settings(max_bot_token="maxtoken", max_target_chat_id=None))
    s = Senders(tg=tg_sender(), max=None, wa=None)
    watcher = MaxChatWatcher(s, rt, poll_timeout=0, idle_delay=7, error_backoff=9)
    assert await watcher.step() == 7  # MAX не подключён — ждём

    s.max = MaxSender(http, MAX, "maxtoken")
    route = respx.get(UPDATES)
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                "marker": 11,
                "updates": [
                    {
                        "update_type": "message_created",
                        "message": {"recipient": {"chat_id": -808, "chat_type": "chat"}},
                    }
                ],
            },
        )
    )
    assert await watcher.step() == 0
    assert await rt.max_known() == {"-808": ""}

    route.mock(
        return_value=httpx.Response(
            200, json={"marker": 12, "updates": [{"update_type": "bot_removed", "chat_id": -808}]}
        )
    )
    assert await watcher.step() == 0
    assert route.calls.last.request.url.params["marker"] == "11"
    assert await rt.max_known() == {}

    route.mock(return_value=httpx.Response(502, json={}))
    assert await watcher.step() == 9  # ошибка — пауза, маркер не теряем
    s.max = MaxSender(http, MAX, "other")  # новый токен — маркер с начала
    route.mock(return_value=httpx.Response(200, json={"updates": [], "marker": 1}))
    await watcher.step()
    assert "marker" not in route.calls.last.request.url.params


async def test_watcher_shutdown_interrupts_poll(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings())
    watcher = MaxChatWatcher(Senders(tg=tg_sender(), max=None, wa=None), rt, idle_delay=3600)
    watcher.start()
    await asyncio.sleep(0)
    await asyncio.wait_for(watcher.shutdown(), timeout=1)


@respx.mock
async def test_known_chats_survive_refresh(storage: Storage, http: httpx.AsyncClient) -> None:
    """«Обновить» не забирает события и не теряет чаты — только проверяет известные."""
    sender = MaxSender(http, MAX, "maxtoken")
    env = await make_env(
        storage, Senders(tg=tg_sender(), max=sender, wa=None), max_target_chat_id=None
    )
    await runtime(env).remember_max_chats({"888": ""}, set())
    updates = respx.get(UPDATES).mock(return_value=httpx.Response(200, json={"updates": []}))
    respx.get(f"{MAX}/chats/888").mock(return_value=httpx.Response(200, json={"title": "Юг"}))
    await click(env, NavCb(to="max_chats").pack())
    await click(env, MaxChatCb(action="refresh").pack())
    assert last_inline(env)[0] == "⬜ Юг"
    assert texts.MAX_CHATS_EMPTY not in last_text(env)
    assert updates.call_count == 0  # события — только фоновому процессу
    await env.bot.session.close()


async def test_new_token_forgets_old_chats(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings(max_bot_token="a", max_target_chat_id=None))
    await rt.set_max_known({"1": "Юг"})
    await rt.set_max_token("a")  # тот же бот — список на месте
    assert await rt.max_known() == {"1": "Юг"}
    await rt.set_max_token("b")  # другой бот — чужие чаты забываем
    assert await rt.max_known() == {}
