"""Клавиатуры: постоянное меню и инлайн-кнопки."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    ChatAdministratorRights,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
)

from app import texts
from app.runtime import DELAY_PRESETS

WA_GROUPS_PAGE_SIZE = 8
# request_id кнопок выбора чата: по нему ответ chat_shared понимает, что выбирали.
REQUEST_TG_CHANNEL = 1
REQUEST_TG_GROUP = 2


class DraftCb(CallbackData, prefix="d"):
    action: str  # toggle | send | cancel | yes | no
    draft_id: int
    net: str = "-"


def preview_kb(
    draft_id: int, available: list[str], selected: set[str], counts: dict[str, int]
) -> InlineKeyboardMarkup:
    toggles = [
        InlineKeyboardButton(
            text=texts.network_button(net, net in selected, counts.get(net, 1)),
            callback_data=DraftCb(action="toggle", draft_id=draft_id, net=net).pack(),
        )
        for net in available
    ]
    actions = [
        InlineKeyboardButton(
            text=texts.BTN_SEND, callback_data=DraftCb(action="send", draft_id=draft_id).pack()
        ),
        InlineKeyboardButton(
            text=texts.BTN_CANCEL,
            callback_data=DraftCb(action="cancel", draft_id=draft_id).pack(),
        ),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[toggles, actions])


def confirm_kb(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_YES,
                    callback_data=DraftCb(action="yes", draft_id=draft_id).pack(),
                ),
                InlineKeyboardButton(
                    text=texts.BTN_NO,
                    callback_data=DraftCb(action="no", draft_id=draft_id).pack(),
                ),
            ]
        ]
    )


def main_menu_kb() -> ReplyKeyboardMarkup:
    rows = [
        [texts.BTN_MENU_POST, texts.BTN_MENU_STOP],
        [texts.BTN_MENU_TG, texts.BTN_MENU_WA, texts.BTN_MENU_MAX],
        [texts.BTN_MENU_SETTINGS, texts.BTN_MENU_HELP],
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER,
    )


# --- разделы: одно сообщение, кнопки переключают экраны ---


class NavCb(CallbackData, prefix="nv"):
    # tg | wa | max | settings | status | delay | history | wa_groups | wa_qr | max_chats
    # | max_connect
    to: str


def _nav(text: str, to: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NavCb(to=to).pack())


def wa_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_nav(texts.BTN_WA_GROUPS, "wa_groups"), _nav(texts.BTN_WA_CONNECT, "wa_qr")]
        ]
    )


def max_section_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_nav(texts.BTN_MAX_CHATS, "max_chats"), _nav(texts.BTN_MAX_CONNECT, "max_connect")]
        ]
    )


def settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _nav(texts.BTN_STATUS, "status"),
                _nav(texts.BTN_DELAY, "delay"),
                _nav(texts.BTN_HISTORY, "history"),
            ]
        ]
    )


def back_kb(back_to: str, *, refresh: tuple[str, str] | None = None) -> InlineKeyboardMarkup:
    """Экран без своих кнопок: «Назад» и, если нужно, «Обновить» (подпись, куда)."""
    row = [_nav(texts.BTN_BACK, back_to)]
    if refresh is not None:
        row.insert(0, _nav(*refresh))
    return InlineKeyboardMarkup(inline_keyboard=[row])


# --- остановка рассылок ---


class StopCb(CallbackData, prefix="sp"):
    action: str  # ask | yes | ask_all | yes_all
    bid: int = 0
    new: int = 0  # 1 — нажата под прогрессом: экран открыть новой панелью


def stop_list_kb(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """items: (номер рассылки, подпись кнопки)."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=StopCb(action="ask", bid=bid).pack())]
        for bid, label in items
    ]
    if len(items) > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.BTN_STOP_ALL, callback_data=StopCb(action="ask_all").pack()
                )
            ]
        )
    rows.append([_nav(texts.BTN_REFRESH, "stop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stop_confirm_kb(bid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_STOP_YES, callback_data=StopCb(action="yes", bid=bid).pack()
                )
            ],
            [_nav(texts.BTN_BACK, "stop")],
        ]
    )


def stop_all_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_STOP_ALL_YES, callback_data=StopCb(action="yes_all").pack()
                )
            ],
            [_nav(texts.BTN_BACK, "stop")],
        ]
    )


def progress_kb(bid: int) -> InlineKeyboardMarkup:
    """Под сообщением-прогрессом идущей рассылки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_STOP_PROGRESS,
                    callback_data=StopCb(action="ask", bid=bid, new=1).pack(),
                )
            ]
        ]
    )


# --- списки с галочками: группы WhatsApp и чаты MAX ---


@dataclass(frozen=True, slots=True)
class ToggleItem:
    id: str
    title: str
    selected: bool
    member: bool  # номер/бот в чате (False — чат выбран, но его больше не видно)


WaGroupItem = ToggleItem


class WaGroupCb(CallbackData, prefix="wg"):
    action: str  # toggle | page | refresh
    gid: str = "-"
    page: int = 0


class MaxChatCb(CallbackData, prefix="mc"):
    action: str  # toggle | page | refresh
    gid: str = "-"
    page: int = 0


def _toggle_kb(
    items: list[ToggleItem],
    page: int,
    pack: Callable[[str, str, int], str],
    label: Callable[[str, bool, bool], str],
    back_to: str,
) -> InlineKeyboardMarkup:
    pages = max(1, -(-len(items) // WA_GROUPS_PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    chunk = items[page * WA_GROUPS_PAGE_SIZE : (page + 1) * WA_GROUPS_PAGE_SIZE]
    rows = [
        [
            InlineKeyboardButton(
                text=label(item.title, item.selected, item.member),
                callback_data=pack("toggle", item.id, page),
            )
        ]
        for item in chunk
    ]
    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.BTN_PREV, callback_data=pack("page", "-", (page - 1) % pages)
                ),
                InlineKeyboardButton(
                    text=f"{page + 1}/{pages}", callback_data=pack("page", "-", page)
                ),
                InlineKeyboardButton(
                    text=texts.BTN_NEXT, callback_data=pack("page", "-", (page + 1) % pages)
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text=texts.BTN_REFRESH, callback_data=pack("refresh", "-", page)),
            _nav(texts.BTN_BACK, back_to),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wa_groups_kb(items: list[ToggleItem], page: int) -> InlineKeyboardMarkup:
    def pack(action: str, gid: str, pg: int) -> str:
        return WaGroupCb(action=action, gid=gid, page=pg).pack()

    return _toggle_kb(items, page, pack, texts.wa_group_button, "wa")


def max_chats_kb(items: list[ToggleItem], page: int) -> InlineKeyboardMarkup:
    def pack(action: str, gid: str, pg: int) -> str:
        return MaxChatCb(action=action, gid=gid, page=pg).pack()

    return _toggle_kb(items, page, pack, texts.max_chat_button, "max")


# --- Telegram: список чатов, удаление, выбор нового чата ---


class TgChatCb(CallbackData, prefix="tc"):
    action: str  # add | ask | del | back | pick
    cid: str = "-"


def tg_offer_kb(cid: str, title: str) -> InlineKeyboardMarkup:
    """Под уведомлением «бота добавили в чат»: сразу включить его в рассылку."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.tg_offer_button(title),
                    callback_data=TgChatCb(action="pick", cid=cid).pack(),
                )
            ]
        ]
    )


def tg_chats_kb(
    chats: dict[str, str], offered: dict[str, str] | None = None
) -> InlineKeyboardMarkup:
    """Экран «Telegram»: нажатие на чат — спросить, убрать ли его из рассылки.

    offered — чаты, где бот есть, но в рассылку они не выбраны: «➕ название» включает.
    """
    rows = [
        [
            InlineKeyboardButton(
                text=texts.tg_chat_button(title),
                callback_data=TgChatCb(action="ask", cid=cid).pack(),
            )
        ]
        for cid, title in chats.items()
    ]
    rows += [
        [
            InlineKeyboardButton(
                text=texts.tg_offer_button(title),
                callback_data=TgChatCb(action="pick", cid=cid).pack(),
            )
        ]
        for cid, title in (offered or {}).items()
    ]
    rows.append(
        [InlineKeyboardButton(text=texts.BTN_TG_ADD, callback_data=TgChatCb(action="add").pack())]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tg_remove_confirm_kb(cid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_TG_REMOVE, callback_data=TgChatCb(action="del", cid=cid).pack()
                ),
                InlineKeyboardButton(
                    text=texts.BTN_CANCEL, callback_data=TgChatCb(action="back").pack()
                ),
            ]
        ]
    )


def _admin_rights(*, post: bool = False, invite: bool = False) -> ChatAdministratorRights:
    """Требуемые права админа: post — публикация (канал), invite — добавлять участников.

    Хотя бы одно право должно быть True, иначе Telegram отвечает ADMIN_RIGHTS_EMPTY.
    """
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=False,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=invite,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_send_welcome_messages=False,
        can_post_messages=post or None,
    )


def tg_pick_kb() -> ReplyKeyboardMarkup:
    """Кнопки выбора чата: Telegram сам покажет список каналов и групп админа.

    Канал — только те, где админ может публиковать; бот добавляется туда админом
    с правом публикации. Группа — только те, где пользователь админ; бот добавляется
    участником.
    """
    channel = KeyboardButton(
        text=texts.BTN_TG_PICK_CHANNEL,
        request_chat=KeyboardButtonRequestChat(
            request_id=REQUEST_TG_CHANNEL,
            chat_is_channel=True,
            user_administrator_rights=_admin_rights(post=True),
            bot_administrator_rights=_admin_rights(post=True),
            request_title=True,
        ),
    )
    group = KeyboardButton(
        text=texts.BTN_TG_PICK_GROUP,
        request_chat=KeyboardButtonRequestChat(
            request_id=REQUEST_TG_GROUP,
            chat_is_channel=False,
            user_administrator_rights=_admin_rights(invite=True),
            bot_is_member=True,
            request_title=True,
        ),
    )
    return ReplyKeyboardMarkup(
        keyboard=[[channel, group], [KeyboardButton(text=texts.BTN_TG_BACK)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=texts.TG_PICK_PLACEHOLDER,
    )


class DelayCb(CallbackData, prefix="dl"):
    key: str


def delays_kb(current: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.delay_button(p.label, texts.DELAY_HINTS[p.key], p.key == current),
                    callback_data=DelayCb(key=p.key).pack(),
                )
            ]
            for p in DELAY_PRESETS
        ]
        + [[_nav(texts.BTN_BACK, "settings")]]
    )
