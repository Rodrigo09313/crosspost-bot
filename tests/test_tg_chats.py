"""Раздел «✈ Telegram»: добавление через выбор чата, удаление, рассылка во все чаты."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
    User,
)

from app import texts
from app.bot.drafts import available_networks, build_plans, target_counts
from app.bot.keyboards import REQUEST_TG_CHANNEL, REQUEST_TG_GROUP, TgChatCb, tg_pick_kb
from app.runtime import RuntimeConfig, Targets
from app.senders.factory import Senders
from app.senders.max import MaxSender
from app.senders.telegram import TelegramSender
from app.senders.wa_evolution import EvolutionSender
from app.storage import Storage
from tests.helpers import ADMIN_ID, STRANGER_ID, message_update
from tests.test_handlers import make_settings
from tests.test_menu import click, last_inline, press, runtime
from tests.test_service_commands import EVO, MAX, Env, make_env

BOT_USER = User(id=42, is_bot=True, first_name="Bot")
CHANNEL = Chat(id=-1001, type="channel", title="Грузы Юг")
GROUP = Chat(id=-1002, type="supergroup", title="Диспетчеры")


def known_chats_sender() -> TelegramSender:
    """Отправщик TG, который видит только CHANNEL и GROUP (для строки статуса)."""
    bot = AsyncMock()
    known = {str(CHANNEL.id): CHANNEL, str(GROUP.id): GROUP}

    async def get_chat(chat_id: str) -> Chat:
        if chat_id not in known:
            raise TelegramBadRequest(AsyncMock(), "chat not found")
        return known[chat_id]

    bot.get_chat.side_effect = get_chat
    return TelegramSender(bot, None)


@pytest.fixture
async def env(storage: Storage, http: httpx.AsyncClient) -> AsyncIterator[Env]:
    senders = Senders(
        tg=known_chats_sender(),
        max=MaxSender(http, MAX, "maxtoken"),
        wa=EvolutionSender(http, EVO, "evo-key", "main", typing=False, qr_interval=0),
    )
    e = await make_env(storage, senders, tg_target_chat_id=None)
    e.bot.chats = {}
    yield e
    await e.bot.session.close()


def admin_member(*, can_post: bool) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        user=BOT_USER,
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=False,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
        can_post_messages=can_post,
    )


async def share(env: Env, chat: Chat, request_id: int, uid: int = ADMIN_ID) -> None:
    shared: dict[str, Any] = {"request_id": request_id, "chat_id": chat.id, "title": chat.title}
    await env.dp.feed_update(env.bot, message_update(uid, chat_shared=shared))


def last_reply_kb(env: Env) -> ReplyKeyboardMarkup:
    for call in reversed(env.bot.calls):
        markup = getattr(call, "reply_markup", None)
        if isinstance(markup, ReplyKeyboardMarkup):
            return markup
    raise AssertionError("нет reply-клавиатуры")


# --- настройки ---


async def test_runtime_seed_add_remove(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings(tg_target_chat_id=-100123))
    assert await rt.tg_chats() == {"-100123": "Telegram"}  # стартовое значение из .env
    assert await rt.add_tg_chat("-1001", "Юг") is True
    assert await rt.add_tg_chat("-1001", "Юг-2") is False
    assert await rt.remove_tg_chat("-100123") == "Telegram"
    assert await rt.remove_tg_chat("-100123") is None
    await rt.update_tg_titles({"-1001": "Юг новый", "-999": "чужой"})

    fresh = RuntimeConfig(storage, make_settings(tg_target_chat_id=-100123))
    assert await fresh.tg_chats() == {"-1001": "Юг новый"}  # из БД, а не из .env


async def test_runtime_empty_list_stays_empty(storage: Storage) -> None:
    rt = RuntimeConfig(storage, make_settings(tg_target_chat_id=-100123))
    await rt.remove_tg_chat("-100123")
    fresh = RuntimeConfig(storage, make_settings(tg_target_chat_id=-100123))
    assert await fresh.tg_chats() == {}  # удалённый .env-чат не возвращается после рестарта


def test_plans_for_all_tg_chats() -> None:
    targets = Targets(tg={"-1001": "Юг", "-1002": "Север"}, max={"777": "MAX"})
    plans = build_plans(targets, {"tg"}, "текст")
    assert [(p.network, p.target_id, p.target_title) for p in plans] == [
        ("tg", "-1001", "Юг"),
        ("tg", "-1002", "Север"),
    ]
    assert available_networks(Targets(max={"777": "MAX"})) == ["max"]
    assert target_counts(targets, {"tg", "max"}) == {"tg": 2, "max": 1}


# --- экран ---


async def test_screen_lists_chats(env: Env) -> None:
    await runtime(env).add_tg_chat("-1001", "Грузы Юг")
    await press(env, texts.BTN_MENU_TG)
    assert "✅ Telegram — «Грузы Юг»" in env.bot.texts()[-1]
    assert last_inline(env) == ["📢 Грузы Юг", texts.BTN_TG_ADD]
    await press(env, texts.LEGACY_TG_CHATS)  # кнопка старого меню открывает тот же раздел
    assert last_inline(env) == ["📢 Грузы Юг", texts.BTN_TG_ADD]


async def test_command_and_empty_screen(env: Env) -> None:
    await env.dp.feed_update(
        env.bot,
        message_update(
            text="/tg_chats", entities=[{"type": "bot_command", "offset": 0, "length": 9}]
        ),
    )
    assert "Telegram — чаты не выбраны" in env.bot.texts()[-1]


async def test_add_shows_picker(env: Env) -> None:
    await click(env, TgChatCb(action="add").pack())
    assert env.bot.texts()[-1] == texts.TG_PICK_HOWTO
    kb = last_reply_kb(env)
    channel, group = kb.keyboard[0]
    assert channel.request_chat is not None and channel.request_chat.chat_is_channel
    assert channel.request_chat.request_id == REQUEST_TG_CHANNEL
    rights = channel.request_chat.bot_administrator_rights
    assert rights is not None and rights.can_post_messages
    assert group.request_chat is not None and not group.request_chat.chat_is_channel
    assert group.request_chat.bot_is_member and group.request_chat.request_id == REQUEST_TG_GROUP
    # в списке групп — только те, где пользователь админ
    assert group.request_chat.user_administrator_rights is not None
    # пустые права Telegram отклоняет (ADMIN_RIGHTS_EMPTY) — хотя бы одно право True
    for button in (channel, group):
        assert button.request_chat is not None
        for rights in (
            button.request_chat.user_administrator_rights,
            button.request_chat.bot_administrator_rights,
        ):
            if rights is not None:
                assert any(v is True for v in rights.model_dump().values())
    assert kb.keyboard[1][0].text == texts.BTN_TG_BACK


async def test_back_returns_main_menu(env: Env) -> None:
    await press(env, texts.BTN_TG_BACK)
    kb = last_reply_kb(env)
    assert kb.keyboard[0][0].text == texts.BTN_MENU_POST
    assert await env.storage.count_queued() == 0  # кнопка — не пост


# --- выбор чата ---


async def test_share_channel_with_post_rights(env: Env) -> None:
    assert env.bot.chats is not None
    env.bot.chats[CHANNEL.id] = CHANNEL
    env.bot.members[CHANNEL.id] = admin_member(can_post=True)
    await share(env, CHANNEL, REQUEST_TG_CHANNEL)
    assert await runtime(env).tg_chats() == {"-1001": "Грузы Юг"}
    assert texts.TG_CHAT_ADDED.format(title="Грузы Юг", count=1) in env.bot.texts()
    assert last_inline(env)[0] == "📢 Грузы Юг"  # сразу показали обновлённый экран


async def test_share_channel_owner_ok(env: Env) -> None:
    assert env.bot.chats is not None
    env.bot.chats[CHANNEL.id] = CHANNEL
    env.bot.members[CHANNEL.id] = ChatMemberOwner(user=BOT_USER, is_anonymous=False)
    await share(env, CHANNEL, REQUEST_TG_CHANNEL)
    assert "-1001" in await runtime(env).tg_chats()


@pytest.mark.parametrize("member", [admin_member(can_post=False), ChatMemberMember(user=BOT_USER)])
async def test_share_channel_without_post_rights(env: Env, member: Any) -> None:
    assert env.bot.chats is not None
    env.bot.chats[CHANNEL.id] = CHANNEL
    env.bot.members[CHANNEL.id] = member
    await share(env, CHANNEL, REQUEST_TG_CHANNEL)
    assert await runtime(env).tg_chats() == {}
    assert env.bot.texts()[-1] == texts.TG_CHAT_NOT_ADMIN.format(title="Грузы Юг")
    assert last_reply_kb(env).keyboard[0][0].text == texts.BTN_TG_PICK_CHANNEL


async def test_share_group(env: Env) -> None:
    assert env.bot.chats is not None
    env.bot.chats[GROUP.id] = GROUP  # участник — права админа не проверяются
    await share(env, GROUP, REQUEST_TG_GROUP)
    assert await runtime(env).tg_chats() == {"-1002": "Диспетчеры"}


async def test_share_inaccessible_chat(env: Env) -> None:
    await share(env, GROUP, REQUEST_TG_GROUP)
    assert await runtime(env).tg_chats() == {}
    assert "Бот не видит этот чат" in env.bot.texts()[-1]


async def test_share_twice(env: Env) -> None:
    assert env.bot.chats is not None
    env.bot.chats[GROUP.id] = GROUP
    await share(env, GROUP, REQUEST_TG_GROUP)
    await share(env, GROUP, REQUEST_TG_GROUP)
    assert texts.TG_CHAT_ALREADY.format(title="Диспетчеры", count=1) in env.bot.texts()


async def test_stranger_share_ignored(env: Env) -> None:
    assert env.bot.chats is not None
    env.bot.chats[GROUP.id] = GROUP
    await share(env, GROUP, REQUEST_TG_GROUP, uid=STRANGER_ID)
    assert await runtime(env).tg_chats() == {}
    assert env.bot.calls == []


# --- удаление ---


async def test_remove_asks_then_removes(env: Env) -> None:
    rt = runtime(env)
    await rt.add_tg_chat("-1001", "Грузы Юг")
    await click(env, TgChatCb(action="ask", cid="-1001").pack())
    assert env.bot.texts()[-1] == texts.tg_remove_ask("Грузы Юг")
    assert last_inline(env) == [texts.BTN_TG_REMOVE, texts.BTN_CANCEL]
    assert "-1001" in await rt.tg_chats()  # пока только спросили

    await click(env, TgChatCb(action="del", cid="-1001").pack())
    assert await rt.tg_chats() == {}
    answers = env.bot.sent("AnswerCallbackQuery")
    assert answers[-1].text == texts.TG_CHAT_REMOVED.format(title="Грузы Юг")
    assert last_inline(env) == [texts.BTN_TG_ADD]


async def test_remove_cancel(env: Env) -> None:
    await runtime(env).add_tg_chat("-1001", "Грузы Юг")
    await click(env, TgChatCb(action="ask", cid="-1001").pack())
    await click(env, TgChatCb(action="back").pack())
    assert "-1001" in await runtime(env).tg_chats()
    assert last_inline(env)[0] == "📢 Грузы Юг"


async def test_add_and_share_keep_one_pick_message(env: Env) -> None:
    """Выбор чата: подсказка с клавиатурой одна, после выбора её сменяет ответ с меню."""
    assert env.bot.chats is not None
    env.bot.chats[GROUP.id] = GROUP
    await click(env, TgChatCb(action="add").pack())
    await click(env, TgChatCb(action="add").pack())  # второй раз — прошлая подсказка удаляется
    await share(env, GROUP, REQUEST_TG_GROUP)
    assert len(env.bot.sent("DeleteMessage")) == 2
    assert last_reply_kb(env).keyboard[0][0].text == texts.BTN_MENU_POST
    # панель (сообщение 5, где нажимали «Добавить») обновилась на месте
    assert env.bot.sent("EditMessageText")[-1].message_id == 5


def test_pick_kb_is_reply_keyboard() -> None:
    assert not isinstance(tg_pick_kb(), InlineKeyboardMarkup)


# --- статус и отчёт ---


async def test_status_lists_each_chat(storage: Storage) -> None:
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest

    from app.senders.telegram import TelegramSender

    tg_bot = AsyncMock()

    async def get_chat(chat_id: str) -> Chat:
        if chat_id == "-1002":
            raise TelegramBadRequest(AsyncMock(), "chat not found")
        return Chat(id=int(chat_id), type="channel", title="Юг (новое имя)")

    tg_bot.get_chat.side_effect = get_chat
    env = await make_env(
        storage, Senders(tg=TelegramSender(tg_bot, None), max=None, wa=None), max_bot_token=None
    )
    rt = runtime(env)
    await rt.remove_tg_chat("-100123")
    await rt.add_tg_chat("-1001", "Юг")
    await rt.add_tg_chat("-1002", "Север")
    await env.cmd("/status")
    text = env.bot.texts()[-1]
    assert "Telegram | ❌ нет доступа: 1 | 2 | 10–30 с" in text
    assert "❌ Telegram «Север» — нет доступа: chat not found" in text
    assert "Юг" not in text  # чат с доступом в проблемы не попадает
    assert (await rt.tg_chats())["-1001"] == "Юг (новое имя)"  # название обновилось
    await env.bot.session.close()


def test_report_counts_tg_chats() -> None:
    rows = [("tg", "Юг", "ok", None), ("tg", "Север", "error", "chat not found")]
    report = texts.report(1, ["tg"], rows, stopped=False)
    assert "❌ Telegram 1/2 чатов" in report
    assert "❌ Север — chat not found" in report
    single = texts.report(1, ["tg"], rows[:1], stopped=False)
    assert "✅ Telegram\n" in single + "\n"


# --- бота добавили в чат (в том числе не админ бота) ---


def my_chat_member(chat: Chat, old: str, new: str, uid: int = STRANGER_ID) -> Update:
    def member(status: str) -> dict[str, Any]:
        data: dict[str, Any] = {"status": status, "user": BOT_USER.model_dump()}
        if status == "administrator":
            data.update(
                admin_member(can_post=True).model_dump(exclude={"user", "status"}),
            )
        if status == "kicked":
            data["until_date"] = 0
        return data

    return Update.model_validate(
        {
            "update_id": 9000 + abs(chat.id) % 1000 + len(old) * 10 + len(new),
            "my_chat_member": {
                "chat": chat.model_dump(),
                "from": {"id": uid, "is_bot": False, "first_name": "Чужой", "last_name": "Админ"},
                "date": 0,
                "old_chat_member": member(old),
                "new_chat_member": member(new),
            },
        }
    )


async def test_bot_added_by_stranger_is_offered(env: Env) -> None:
    env.bot.chats = {CHANNEL.id: CHANNEL}
    env.bot.members = {CHANNEL.id: admin_member(can_post=True)}
    await env.dp.feed_update(env.bot, my_chat_member(CHANNEL, "left", "administrator"))
    rt = runtime(env)
    assert await rt.tg_known() == {str(CHANNEL.id): "Грузы Юг"}
    assert await rt.tg_chats() == {}  # само в рассылку не попадает
    note = env.bot.sent("SendMessage")[-1]
    assert note.chat_id == ADMIN_ID
    assert "Бота добавили в канал «Грузы Юг», добавил: Чужой Админ" in note.text
    assert [b.text for row in note.reply_markup.inline_keyboard for b in row] == ["➕ Грузы Юг"]

    await press(env, texts.BTN_MENU_TG)
    assert "➕ Грузы Юг" in last_inline(env)
    await click(env, TgChatCb(action="pick", cid=str(CHANNEL.id)).pack())
    assert await rt.tg_chats() == {str(CHANNEL.id): "Грузы Юг"}
    assert "➕ Грузы Юг" not in last_inline(env)
    assert "📢 Грузы Юг" in last_inline(env)


async def test_offered_channel_without_post_right(env: Env) -> None:
    env.bot.chats = {CHANNEL.id: CHANNEL}
    env.bot.members = {CHANNEL.id: admin_member(can_post=False)}
    await env.dp.feed_update(env.bot, my_chat_member(CHANNEL, "left", "administrator"))
    await click(env, TgChatCb(action="pick", cid=str(CHANNEL.id)).pack())
    assert await runtime(env).tg_chats() == {}
    assert "бот не админ с правом публикации" in env.bot.texts()[-1]


async def test_bot_removed_forgets_chat(env: Env) -> None:
    await env.dp.feed_update(env.bot, my_chat_member(GROUP, "left", "member"))
    assert await runtime(env).tg_known() == {str(GROUP.id): "Диспетчеры"}
    notes = len(env.bot.sent("SendMessage"))
    await env.dp.feed_update(env.bot, my_chat_member(GROUP, "member", "kicked"))
    assert await runtime(env).tg_known() == {}
    assert len(env.bot.sent("SendMessage")) == notes  # об удалении не пишем


async def test_private_chat_membership_ignored(env: Env) -> None:
    private = Chat(id=STRANGER_ID, type="private", first_name="Чужой")
    await env.dp.feed_update(env.bot, my_chat_member(private, "kicked", "member"))
    assert await runtime(env).tg_known() == {}
    assert env.bot.sent("SendMessage") == []
