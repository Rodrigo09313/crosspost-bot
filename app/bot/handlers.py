"""Обработчики: приём поста, предпросмотр, кнопки, подтверждение."""

from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, MessageOriginChannel

from app import texts
from app.bot import errors, menu
from app.bot.deps import BroadcastQueue, WaTitlesProvider
from app.bot.drafts import (
    AlbumTracker,
    DraftStore,
    available_networks,
    build_plans,
    estimate_minutes,
    network_counts,
    target_counts,
)
from app.bot.keyboards import (
    DelayCb,
    DraftCb,
    MaxChatCb,
    NavCb,
    StopCb,
    TgChatCb,
    WaGroupCb,
    confirm_kb,
    preview_kb,
)
from app.config import Settings
from app.runtime import RuntimeConfig
from app.splitter import split_text
from app.storage import Storage

log = logging.getLogger(__name__)

TG_TEXT_LIMIT = 4096
DUPLICATE_WINDOW_HOURS = 12  # предупреждать о таком же посте за последние N часов


# --- группа ---


async def cmd_here(message: Message) -> None:
    await message.reply(texts.HERE_ID.format(chat_id=message.chat.id))


# --- команды в личке ---


# --- приём поста ---


async def forwarded_from_channel(message: Message, origin: MessageOriginChannel) -> None:
    title = origin.chat.title or ""
    await message.answer(texts.CHANNEL_ID.format(title=escape(title), chat_id=origin.chat.id))


async def album_refused(message: Message, albums: AlbumTracker) -> None:
    if message.media_group_id and albums.first_seen(message.media_group_id):
        await message.answer(texts.ONLY_TEXT)


async def unknown_command(message: Message) -> None:
    await message.answer(texts.UNKNOWN_COMMAND)


async def receive_post(
    message: Message, settings: Settings, drafts: DraftStore, runtime: RuntimeConfig
) -> None:
    text = message.text or ""
    if len(text) > TG_TEXT_LIMIT:
        await message.answer(texts.TOO_LONG.format(limit=TG_TEXT_LIMIT))
        return
    targets = await runtime.targets()
    available = available_networks(targets)
    if not available:
        await message.answer(texts.NO_NETWORKS_CONFIGURED)
        return
    assert message.from_user is not None
    draft = drafts.add(
        admin_id=message.from_user.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        text=text,
        networks=set(available),
    )
    await message.answer(
        texts.preview(text, max_parts=len(split_text(text))),
        reply_markup=preview_kb(draft.id, available, draft.networks, network_counts(targets)),
    )


async def non_text_refused(message: Message) -> None:
    await message.answer(texts.ONLY_TEXT)


# --- кнопки черновика ---


async def on_draft_button(
    cb: CallbackQuery,
    callback_data: DraftCb,
    settings: Settings,
    db: Storage,
    drafts: DraftStore,
    queue: BroadcastQueue,
    wa_titles: WaTitlesProvider,
    runtime: RuntimeConfig,
) -> None:
    draft = drafts.get(callback_data.draft_id, cb.from_user.id)
    message = cb.message if isinstance(cb.message, Message) else None
    if draft is None or message is None:
        await cb.answer(texts.DRAFT_EXPIRED, show_alert=True)
        return

    targets = await runtime.targets()
    available = available_networks(targets)
    counts = network_counts(targets)
    action = callback_data.action

    if action == "toggle":
        if callback_data.net in available:
            draft.networks ^= {callback_data.net}
        await message.edit_reply_markup(
            reply_markup=preview_kb(draft.id, available, draft.networks, counts)
        )
        await cb.answer()
    elif action == "cancel":
        drafts.pop(draft.id)
        await message.edit_text(texts.CANCELLED)
        await cb.answer()
    elif action == "send":
        selected = draft.networks & set(available)
        if not selected:
            await cb.answer(texts.SELECT_NETWORK, show_alert=True)
            return
        ahead = sum(1 for b in await db.unfinished() if selected & set(b.networks))
        same = await db.same_text_recent(draft.text, DUPLICATE_WINDOW_HOURS)
        await message.edit_text(
            texts.confirm(
                target_counts(targets, selected),
                estimate_minutes(settings, target_counts(targets, selected), await runtime.delay()),
                ahead=ahead,
                duplicates=[(b.id, texts.when(b.created_at), b.status) for b in same],
            ),
            reply_markup=confirm_kb(draft.id),
        )
        await cb.answer()
    elif action == "no":
        await message.edit_text(
            texts.preview(draft.text, max_parts=len(split_text(draft.text))),
            reply_markup=preview_kb(draft.id, available, draft.networks, counts),
        )
        await cb.answer()
    elif action == "yes":
        selected = draft.networks & set(available)
        if not selected:
            await cb.answer(texts.SELECT_NETWORK, show_alert=True)
            return
        drafts.pop(draft.id)
        if "wa" in selected:
            await runtime.update_titles(await wa_titles())
            targets = await runtime.targets()
        broadcast_id = await db.create_broadcast(
            admin_id=draft.admin_id,
            source_chat_id=draft.chat_id,
            source_message_id=draft.message_id,
            text=draft.text,
            networks=[n for n in available if n in selected],
            deliveries=build_plans(targets, selected, draft.text),
        )
        log.info("Рассылка #%s создана: %s", broadcast_id, sorted(selected))
        ahead = sum(1 for b in await db.unfinished() if b.id < broadcast_id)
        await message.edit_text(
            texts.QUEUED_AHEAD.format(id=broadcast_id, ahead=ahead)
            if ahead > 0
            else texts.QUEUED.format(id=broadcast_id)
        )
        await cb.answer()
        queue.wake()
    else:
        await cb.answer()


def setup_routers() -> list[Router]:
    """Создаёт роутеры. Порядок важен: команды раньше «поймать всё» для постов."""
    group = Router(name="group")
    group.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    group.message.register(cmd_here, Command("here"))

    commands = Router(name="commands")
    commands.message.filter(F.chat.type == ChatType.PRIVATE)
    commands.message.register(menu.menu_start, CommandStart())
    commands.message.register(menu.open_stop, Command("stop"))
    for names, section in (
        (("tg", "tg_chats"), "tg"),
        (("wa",), "wa"),
        (("wa_groups",), "wa_groups"),
        (("wa_qr",), "wa_qr"),
        (("max",), "max"),
        (("max_chats",), "max_chats"),
        (("status",), "status"),
        (("pause",), "delay"),
        (("history",), "history"),
        (("settings",), "settings"),
    ):
        commands.message.register(menu.opener(section), Command(*names))

    # Кнопки меню приходят обычным текстом — ловим их раньше, чем приём поста.
    buttons = Router(name="menu")
    buttons.message.filter(F.chat.type == ChatType.PRIVATE)
    buttons.message.register(menu.menu_post, F.text.in_({texts.BTN_MENU_POST, texts.LEGACY_POST}))
    buttons.message.register(menu.open_stop, F.text == texts.BTN_MENU_STOP)
    buttons.message.register(menu.menu_help, F.text == texts.BTN_MENU_HELP)
    buttons.message.register(menu.menu_tg_back, F.text == texts.BTN_TG_BACK)
    for label, section in (
        (texts.BTN_MENU_TG, "tg"),
        (texts.BTN_MENU_WA, "wa"),
        (texts.BTN_MENU_MAX, "max"),
        (texts.BTN_MENU_SETTINGS, "settings"),
        # прошлые версии меню
        (texts.LEGACY_TG_CHATS, "tg"),
        (texts.LEGACY_WA_GROUPS, "wa_groups"),
        (texts.LEGACY_WA_CONNECT, "wa_qr"),
        (texts.LEGACY_STATUS, "status"),
        (texts.LEGACY_DELAY, "delay"),
        (texts.LEGACY_HISTORY, "history"),
    ):
        buttons.message.register(menu.opener(section), F.text == label)
    # Токен MAX — после кнопок меню: нажатие кнопки отменяет ожидание токена.
    buttons.message.register(menu.on_max_token, F.text, menu.awaiting_max_token)
    buttons.message.register(menu.on_chat_shared, F.chat_shared)
    buttons.my_chat_member.register(menu.on_my_chat_member)
    buttons.callback_query.register(menu.on_nav, NavCb.filter())
    buttons.callback_query.register(menu.on_tg_chat_button, TgChatCb.filter())
    buttons.callback_query.register(menu.on_wa_group_button, WaGroupCb.filter())
    buttons.callback_query.register(menu.on_max_chat_button, MaxChatCb.filter())
    buttons.callback_query.register(menu.on_delay_button, DelayCb.filter())
    buttons.callback_query.register(menu.on_stop_button, StopCb.filter())

    posts = Router(name="posts")
    posts.message.filter(F.chat.type == ChatType.PRIVATE)
    posts.message.register(
        forwarded_from_channel, F.forward_origin.as_("origin"), F.forward_origin.type == "channel"
    )
    posts.message.register(album_refused, F.media_group_id)
    posts.message.register(unknown_command, F.text.startswith("/"))
    posts.message.register(receive_post, F.text)
    posts.message.register(non_text_refused)
    posts.callback_query.register(on_draft_button, DraftCb.filter())
    posts.callback_query.register(errors.stale_button)  # последним: всё, что не поймали выше
    return [group, commands, buttons, posts]
