"""Меню бота: разделы площадок и настроек в одном сообщении-панели.

Нижнее меню открывает раздел — бот присылает панель и удаляет прошлую. Кнопки внутри
панели не шлют новых сообщений, а меняют эту же панель («↩ Назад» — шаг назад).
Отдельными сообщениями остаются только QR-код WhatsApp (картинка) и клавиатура
выбора чата Telegram — бот убирает их, когда они больше не нужны.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ChatMemberAdministrator,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    ReplyKeyboardMarkup,
)

from app import texts
from app.bot import service
from app.bot.deps import BroadcastQueue
from app.bot.drafts import estimate_minutes, network_counts
from app.bot.keyboards import (
    DelayCb,
    MaxChatCb,
    NavCb,
    StopCb,
    TgChatCb,
    ToggleItem,
    WaGroupCb,
    back_kb,
    delays_kb,
    main_menu_kb,
    max_chats_kb,
    max_section_kb,
    settings_kb,
    stop_all_confirm_kb,
    stop_confirm_kb,
    stop_list_kb,
    tg_chats_kb,
    tg_offer_kb,
    tg_pick_kb,
    tg_remove_confirm_kb,
    wa_groups_kb,
    wa_section_kb,
)
from app.config import Settings
from app.logging_setup import add_secret
from app.runtime import RuntimeConfig
from app.senders.base import ChatInfo, SenderError
from app.senders.factory import MaxFactory, Senders
from app.storage import Broadcast, Storage

log = logging.getLogger(__name__)

Markup = InlineKeyboardMarkup | None
Content = str | InputRichMessage  # обычный текст или rich-сообщение (таблицы)


# --- состояние в памяти ---


class ChatCatalog:
    """Последний полученный список чатов: {id: название} или ошибка запроса."""

    def __init__(self) -> None:
        self.groups: dict[str, str] = {}
        self.error: str | None = None

    async def load(
        self, fetch: Callable[[], Awaitable[list[ChatInfo]]] | None, missing: str
    ) -> None:
        if fetch is None:
            self.groups, self.error = {}, missing
            return
        try:
            chats = await fetch()
        except SenderError as exc:
            self.error = str(exc)
            return
        self.groups = {c.id: c.title for c in chats}
        self.error = None


class WaCatalog(ChatCatalog):
    """Группы номера WhatsApp.

    Запрос долгий (≈1,5 мин при ~300 группах) и нагружает WhatsApp (rate-overlimit),
    поэтому список хранится в SQLite и заново запрашивается только по «🔄 Обновить».
    Одновременно идёт не больше одного запроса.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fetched_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def ensure(self, runtime: RuntimeConfig) -> bool:
        """Подтянуть сохранённый список. True — список есть, запрашивать не нужно."""
        if self.fetched_at is None:
            cached = await runtime.wa_catalog()
            if cached is not None:
                self.groups, self.fetched_at = cached
        return self.fetched_at is not None

    async def refresh(self, senders: Senders, runtime: RuntimeConfig) -> None:
        if self._lock.locked():  # список уже грузится — дождаться того же результата
            async with self._lock:
                return
        async with self._lock:
            fetch = senders.wa.list_groups if senders.wa else None
            await self.load(fetch, texts.WA_NOT_CONFIGURED)
            if self.error is None:
                self.fetched_at = datetime.now(UTC)
                await runtime.save_wa_catalog(self.groups)


class MaxCatalog(ChatCatalog):
    """Чаты MAX. Бот помнит все чаты, где его видел, — список не пустеет со временем."""

    async def refresh(self, senders: Senders, runtime: RuntimeConfig) -> None:
        sender = senders.max
        if sender is None:
            await self.load(None, texts.MAX_NOT_CONNECTED)
            return
        known = await runtime.max_known()

        async def fetch() -> list[ChatInfo]:
            return await sender.list_chats(known)

        await self.load(fetch, texts.MAX_NOT_CONNECTED)
        if self.error is None:
            await runtime.set_max_known(dict(self.groups))


class PendingInput:
    """Кто из админов сейчас должен прислать токен MAX следующим сообщением."""

    def __init__(self) -> None:
        self._users: set[int] = set()

    def expect(self, user_id: int) -> None:
        self._users.add(user_id)

    def clear(self, user_id: int) -> None:
        self._users.discard(user_id)

    def waiting(self, user_id: int) -> bool:
        return user_id in self._users


class Screens:
    """Одна панель меню на чат + вспомогательные сообщения (QR, выбор чата).

    Помнит только в памяти: панели, открытые до перезапуска, остаются в чате, их кнопки
    работают (состояние берётся из БД) и становятся текущей панелью при нажатии.
    """

    PANEL = "panel"

    def __init__(self) -> None:
        self._ids: dict[tuple[int, str], int] = {}

    def remember(self, chat_id: int, key: str, message_id: int) -> None:
        self._ids[(chat_id, key)] = message_id

    async def drop(self, bot: Bot, chat_id: int, key: str) -> None:
        old = self._ids.pop((chat_id, key), None)
        if old is not None:
            # уже удалено руками или старше 48 ч — не важно
            with contextlib.suppress(TelegramAPIError):
                await bot.delete_message(chat_id=chat_id, message_id=old)

    async def send(
        self,
        bot: Bot,
        chat_id: int,
        key: str,
        text: Content,
        markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None,
    ) -> None:
        """Новое сообщение вместо прошлого с тем же ключом."""
        if isinstance(text, InputRichMessage):
            sent = await bot.send_rich_message(
                chat_id=chat_id, rich_message=text, reply_markup=markup
            )
        else:
            sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        await self.drop(bot, chat_id, key)
        self.remember(chat_id, key, sent.message_id)

    async def show(self, bot: Bot, chat_id: int, text: Content, markup: Markup) -> None:
        """Новая панель внизу чата, прошлая удаляется."""
        await self.send(bot, chat_id, self.PANEL, text, markup)

    async def edit(self, bot: Bot, chat_id: int, text: Content, markup: Markup) -> None:
        """Поменять текущую панель; если её нет или она недоступна — прислать новую.

        Обычная панель и rich-таблица меняются друг на друга той же правкой.
        """
        panel = self._ids.get((chat_id, self.PANEL))
        if panel is not None:
            try:
                if isinstance(text, InputRichMessage):
                    await bot.edit_message_text(
                        rich_message=text, chat_id=chat_id, message_id=panel, reply_markup=markup
                    )
                else:
                    await bot.edit_message_text(
                        text=text, chat_id=chat_id, message_id=panel, reply_markup=markup
                    )
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in exc.message:
                    return
        await self.show(bot, chat_id, text, markup)


@dataclass(slots=True)
class Ctx:
    """Всё, что нужно экранам меню (зависимости обработчика)."""

    bot: Bot
    settings: Settings
    db: Storage
    senders: Senders
    runtime: RuntimeConfig
    wa_catalog: WaCatalog
    max_catalog: MaxCatalog
    screens: Screens
    pending: PendingInput
    queue: BroadcastQueue | None = None  # нужен только экрану «Стоп»


@dataclass(slots=True)
class View:
    text: Content
    markup: Markup
    qr: bytes | None = None  # QR WhatsApp — отдельной картинкой под панелью


# --- экраны ---


def _toggle_items(selected: dict[str, str], catalog: ChatCatalog) -> tuple[list[ToggleItem], bool]:
    known = catalog.error is None
    items = [ToggleItem(i, t, i in selected, True) for i, t in catalog.groups.items()]
    items += [
        ToggleItem(i, t, True, not known) for i, t in selected.items() if i not in catalog.groups
    ]
    items.sort(key=lambda i: (i.title.lower(), i.id))
    return items, known and not catalog.groups


async def _wa_groups_view(ctx: Ctx, page: int) -> View:
    selected = await ctx.runtime.wa_groups()
    items, empty = _toggle_items(selected, ctx.wa_catalog)
    text = texts.wa_groups_screen(
        selected=len(selected),
        total=len(items),
        error=ctx.wa_catalog.error,
        empty=empty,
        updated=ctx.wa_catalog.fetched_at,
    )
    return View(text, wa_groups_kb(items, page))


async def _max_chats_view(ctx: Ctx, page: int) -> View:
    selected = await ctx.runtime.max_chats()
    items, empty = _toggle_items(selected, ctx.max_catalog)
    text = texts.max_chats_screen(
        selected=len(selected), total=len(items), error=ctx.max_catalog.error, empty=empty
    )
    return View(text, max_chats_kb(items, page))


async def _tg_view(ctx: Ctx) -> View:
    line = await service.tg_line(ctx.senders, ctx.runtime)
    return View(*await _tg_screen(line, ctx.runtime))


async def _delay_view(ctx: Ctx) -> View:
    preset = await ctx.runtime.delay()
    counts = network_counts(await ctx.runtime.targets())
    minutes = estimate_minutes(ctx.settings, counts, preset)
    text = texts.delay_screen(current=preset.label, counts=counts, minutes=minutes)
    return View(text, delays_kb(preset.key))


async def _wa_qr_view(ctx: Ctx) -> View:
    back = back_kb("wa")
    if ctx.senders.wa is None:
        return View(texts.WA_NOT_CONFIGURED, back)
    try:
        qr = await ctx.senders.wa.get_qr()
    except SenderError as exc:
        return View(service.error_text(texts.WA_QR_ERROR, exc), back)
    if qr.already_connected or qr.png is None:
        return View(texts.WA_ALREADY_CONNECTED, back)
    return View(
        texts.WA_CONNECT_HOWTO, back_kb("wa", refresh=(texts.BTN_WA_NEW_QR, "wa_qr")), qr.png
    )


async def render(to: str, ctx: Ctx) -> View:
    if to == "tg":
        return await _tg_view(ctx)
    if to == "wa":
        line = await service.wa_line(ctx.senders, ctx.runtime)
        return View(texts.wa_section(line), wa_section_kb())
    if to == "wa_groups":
        if ctx.senders.wa is None:
            return View(texts.WA_NOT_CONFIGURED, back_kb("wa"))
        if not await ctx.wa_catalog.ensure(ctx.runtime):
            await ctx.wa_catalog.refresh(ctx.senders, ctx.runtime)
        await ctx.runtime.update_titles(ctx.wa_catalog.groups)
        return await _wa_groups_view(ctx, 0)
    if to == "wa_qr":
        return await _wa_qr_view(ctx)
    if to == "max":
        line = await service.max_line(ctx.senders, ctx.runtime)
        return View(texts.max_section(line), max_section_kb())
    if to == "max_chats":
        if ctx.senders.max is None:
            return View(texts.MAX_NOT_CONNECTED, back_kb("max"))
        await ctx.max_catalog.refresh(ctx.senders, ctx.runtime)
        await ctx.runtime.update_max_titles(ctx.max_catalog.groups)
        return await _max_chats_view(ctx, 0)
    if to == "max_connect":
        return View(texts.MAX_TOKEN_ASK, back_kb("max"))
    if to == "status":
        status = await service.status_view(ctx.db, ctx.senders, ctx.runtime)
        return View(status, back_kb("settings", refresh=(texts.BTN_REFRESH, "status")))
    if to == "delay":
        return await _delay_view(ctx)
    if to == "history":
        return View(await service.history_view(ctx.db), back_kb("settings"))
    if to == "stop":
        return await _stop_view(ctx.db, ctx.queue)
    return View(texts.SETTINGS, settings_kb())


async def _go(ctx: Ctx, chat_id: int, user_id: int, to: str, *, new: bool) -> None:
    """Показать экран: new — новой панелью внизу, иначе поменять текущую."""
    await ctx.screens.drop(ctx.bot, chat_id, "qr")
    if to == "max_connect":
        ctx.pending.expect(user_id)
    else:
        ctx.pending.clear(user_id)
    if to == "wa_groups" and ctx.senders.wa and not await ctx.wa_catalog.ensure(ctx.runtime):
        # Первый запрос групп идёт до ~1,5 мин — сразу показать, что бот работает.
        if new:
            await ctx.screens.show(ctx.bot, chat_id, texts.WA_GROUPS_LOADING, None)
            new = False
        else:
            await ctx.screens.edit(ctx.bot, chat_id, texts.WA_GROUPS_LOADING, None)
    view = await render(to, ctx)
    await _present(ctx, chat_id, view, new=new)


async def _present(ctx: Ctx, chat_id: int, view: View, *, new: bool) -> None:
    if new:
        await ctx.screens.show(ctx.bot, chat_id, view.text, view.markup)
    else:
        await ctx.screens.edit(ctx.bot, chat_id, view.text, view.markup)
    if view.qr is not None:
        photo = await ctx.bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(view.qr, "wa_qr.png"),
            caption=texts.WA_QR_CAPTION,
        )
        ctx.screens.remember(chat_id, "qr", photo.message_id)


# --- остановка рассылок ---


async def _stoppable(db: Storage, queue: BroadcastQueue | None) -> list[Broadcast]:
    stopped = queue.stopped_ids if queue is not None else set()
    return [b for b in await db.unfinished() if b.id not in stopped]


async def _stop_view(db: Storage, queue: BroadcastQueue | None) -> View:
    items = [
        (
            b.id,
            texts.stop_button(
                b.id, texts.post_title(b.text), texts.stop_state(await db.network_counts(b.id))
            ),
        )
        for b in await _stoppable(db, queue)
    ]
    return View(texts.stop_screen(len(items)), stop_list_kb(items))


async def open_stop(
    message: Message, bot: Bot, db: Storage, screens: Screens, queue: BroadcastQueue
) -> None:
    """«⏹ Стоп» и /stop: список идущих и ждущих рассылок новой панелью."""
    view = await _stop_view(db, queue)
    await screens.show(bot, message.chat.id, view.text, view.markup)


async def on_stop_button(
    cb: CallbackQuery,
    callback_data: StopCb,
    bot: Bot,
    db: Storage,
    screens: Screens,
    queue: BroadcastQueue,
) -> None:
    """Остановка одной рассылки или всех — с подтверждением."""
    message = cb.message if isinstance(cb.message, Message) else None
    if message is None:
        await cb.answer()
        return
    chat_id = message.chat.id
    new = bool(callback_data.new)  # из прогресса — новой панелью, прогресс не трогаем
    if not new:
        screens.remember(chat_id, Screens.PANEL, message.message_id)
    action, bid = callback_data.action, callback_data.bid
    notice: str | None = None
    view: View | None = None
    if action == "ask":
        broadcast = next((b for b in await _stoppable(db, queue) if b.id == bid), None)
        if broadcast is None:
            notice = texts.STOP_GONE.format(id=bid)
        else:
            counts = await db.network_counts(bid)
            text = texts.stop_confirm(bid, texts.post_title(broadcast.text, 60), counts)
            view = View(text, stop_confirm_kb(bid))
    elif action == "yes":
        stopped = await queue.stop([bid])
        notice = (texts.STOP_DONE if stopped else texts.STOP_GONE).format(id=bid)
    elif action == "ask_all":
        count = len(await _stoppable(db, queue))
        if count:
            view = View(texts.stop_all_confirm(count), stop_all_confirm_kb())
    elif action == "yes_all":
        stopped = await queue.stop()
        notice = texts.STOP_ALL_DONE.format(n=len(stopped))
    await cb.answer(notice)
    view = view or await _stop_view(db, queue)
    if new:
        await screens.show(bot, chat_id, view.text, view.markup)
    else:
        await screens.edit(bot, chat_id, view.text, view.markup)


# --- открытие разделов из нижнего меню и командами ---

OpenHandler = Callable[..., Awaitable[None]]


def opener(to: str) -> OpenHandler:
    """Обработчик кнопки нижнего меню или команды: открыть раздел новой панелью."""

    async def handler(
        message: Message,
        bot: Bot,
        settings: Settings,
        db: Storage,
        senders: Senders,
        runtime: RuntimeConfig,
        wa_catalog: WaCatalog,
        max_catalog: MaxCatalog,
        screens: Screens,
        pending: PendingInput,
        queue: BroadcastQueue,
    ) -> None:
        ctx = Ctx(
            bot, settings, db, senders, runtime, wa_catalog, max_catalog, screens, pending, queue
        )
        assert message.from_user is not None
        await _go(ctx, message.chat.id, message.from_user.id, to, new=True)

    handler.__name__ = f"open_{to}"
    return handler


async def on_nav(
    cb: CallbackQuery,
    callback_data: NavCb,
    bot: Bot,
    settings: Settings,
    db: Storage,
    senders: Senders,
    runtime: RuntimeConfig,
    wa_catalog: WaCatalog,
    max_catalog: MaxCatalog,
    screens: Screens,
    pending: PendingInput,
    queue: BroadcastQueue,
) -> None:
    # Запросы к площадкам бывают долгими, а Telegram ждёт ответа на кнопку ~15 с.
    await cb.answer()
    message = cb.message if isinstance(cb.message, Message) else None
    if message is None:
        return
    screens.remember(message.chat.id, Screens.PANEL, message.message_id)
    ctx = Ctx(bot, settings, db, senders, runtime, wa_catalog, max_catalog, screens, pending, queue)
    await _go(ctx, message.chat.id, cb.from_user.id, callback_data.to, new=False)


# --- простые кнопки ---


async def menu_start(message: Message) -> None:
    await message.answer(texts.START, reply_markup=main_menu_kb())


async def menu_help(message: Message) -> None:
    await message.answer(texts.HELP, reply_markup=main_menu_kb())


async def menu_post(message: Message) -> None:
    await message.answer(texts.POST_HOWTO)


# --- списки с галочками: группы WhatsApp и чаты MAX ---


async def _edit(message: Message, text: Content, markup: Markup) -> None:
    try:
        if isinstance(text, InputRichMessage):
            await message.edit_text(rich_message=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            raise


async def on_wa_group_button(
    cb: CallbackQuery,
    callback_data: WaGroupCb,
    bot: Bot,
    settings: Settings,
    db: Storage,
    senders: Senders,
    runtime: RuntimeConfig,
    wa_catalog: WaCatalog,
    max_catalog: MaxCatalog,
    screens: Screens,
    pending: PendingInput,
) -> None:
    ctx = Ctx(bot, settings, db, senders, runtime, wa_catalog, max_catalog, screens, pending)

    async def toggle(gid: str) -> None:
        selected = await runtime.wa_groups()
        title = wa_catalog.groups.get(gid) or selected.get(gid) or gid
        await runtime.set_wa_group(gid, title, selected=gid not in selected)

    async def refresh() -> None:
        if isinstance(cb.message, Message):
            await _edit(cb.message, texts.WA_GROUPS_LOADING, None)
        await wa_catalog.refresh(senders, runtime)
        await runtime.update_titles(wa_catalog.groups)

    await wa_catalog.ensure(runtime)  # после перезапуска бота — список из базы, без запроса
    await _on_toggle(
        cb, callback_data, wa_catalog, toggle, refresh, lambda p: _wa_groups_view(ctx, p)
    )


async def on_max_chat_button(
    cb: CallbackQuery,
    callback_data: MaxChatCb,
    bot: Bot,
    settings: Settings,
    db: Storage,
    senders: Senders,
    runtime: RuntimeConfig,
    wa_catalog: WaCatalog,
    max_catalog: MaxCatalog,
    screens: Screens,
    pending: PendingInput,
) -> None:
    ctx = Ctx(bot, settings, db, senders, runtime, wa_catalog, max_catalog, screens, pending)

    async def toggle(cid: str) -> None:
        selected = await runtime.max_chats()
        title = max_catalog.groups.get(cid) or selected.get(cid) or cid
        await runtime.set_max_chat(cid, title, selected=cid not in selected)

    async def refresh() -> None:
        await max_catalog.refresh(senders, runtime)
        await runtime.update_max_titles(max_catalog.groups)

    await _on_toggle(
        cb, callback_data, max_catalog, toggle, refresh, lambda p: _max_chats_view(ctx, p)
    )


async def _on_toggle(
    cb: CallbackQuery,
    data: WaGroupCb | MaxChatCb,
    catalog: ChatCatalog,
    toggle: Callable[[str], Awaitable[None]],
    refresh: Callable[[], Awaitable[None]],
    view: Callable[[int], Awaitable[View]],
) -> None:
    message = cb.message if isinstance(cb.message, Message) else None
    if message is None:
        await cb.answer()
        return
    # После перезапуска бота каталог пуст, а экран остался — при листании подтянуть список.
    stale = data.action == "page" and not catalog.groups and catalog.error is None
    fetch = data.action == "refresh" or stale
    if fetch:
        # Запрос к площадке может идти до 30 с, а Telegram ждёт ответа на кнопку ~15 с.
        await cb.answer(texts.REFRESHING)
        await refresh()
    elif data.action == "toggle":
        await toggle(data.gid)
    shown = await view(data.page)
    await _edit(message, shown.text, shown.markup)
    if not fetch:
        await cb.answer()


# --- паузы ---


async def on_delay_button(
    cb: CallbackQuery, callback_data: DelayCb, settings: Settings, runtime: RuntimeConfig
) -> None:
    try:
        preset = await runtime.set_delay(callback_data.key)
    except ValueError:
        await cb.answer()
        return
    if isinstance(cb.message, Message):
        counts = network_counts(await runtime.targets())
        minutes = estimate_minutes(settings, counts, preset)
        text = texts.delay_screen(current=preset.label, counts=counts, minutes=minutes)
        await _edit(cb.message, text, delays_kb(preset.key))
    await cb.answer(texts.DELAY_SET.format(label=preset.label))


# --- Telegram: добавление выбором чата и удаление ---


async def menu_tg_back(message: Message, bot: Bot, screens: Screens) -> None:
    """«↩ Назад в меню» на клавиатуре выбора чата: вернуть нижнее меню."""
    await screens.send(bot, message.chat.id, "pick", texts.TG_BACK, main_menu_kb())


async def on_tg_chat_button(
    cb: CallbackQuery,
    callback_data: TgChatCb,
    bot: Bot,
    senders: Senders,
    runtime: RuntimeConfig,
    screens: Screens,
) -> None:
    message = cb.message if isinstance(cb.message, Message) else None
    if message is None:
        await cb.answer()
        return
    screens.remember(message.chat.id, Screens.PANEL, message.message_id)
    action = callback_data.action
    if action == "add":
        # Кнопки выбора чата живут только в нижней клавиатуре — отдельным сообщением.
        await screens.send(bot, message.chat.id, "pick", texts.TG_PICK_HOWTO, tg_pick_kb())
        await cb.answer()
        return
    if action == "ask":
        title = (await runtime.tg_chats()).get(callback_data.cid)
        if title is not None:
            await _edit(
                message, texts.tg_remove_ask(title), tg_remove_confirm_kb(callback_data.cid)
            )
            await cb.answer()
            return
    notice: str | None = None
    error: str | None = None
    if action == "pick":
        known = await runtime.tg_known()
        cid = callback_data.cid
        error, title = await _check_bot_access(bot, int(cid), known.get(cid, cid))
        if error is None:
            await runtime.add_tg_chat(cid, title)
            notice = texts.TG_CHAT_ADDED.format(title=title, count=len(await runtime.tg_chats()))
    if action == "del":
        title = await runtime.remove_tg_chat(callback_data.cid)
        if title is not None:
            notice = texts.TG_CHAT_REMOVED.format(title=title)
    line = await service.tg_line(senders, runtime)
    text, kb = await _tg_screen(line, runtime)
    await _edit(message, f"{text}\n\n{error}" if error else text, kb)
    await cb.answer(notice)


async def _tg_screen(line: str, runtime: RuntimeConfig) -> tuple[str, InlineKeyboardMarkup]:
    """Экран «Telegram»: выбранные чаты и чаты, куда бота добавили, но не выбранные."""
    chats = await runtime.tg_chats()
    offered = {cid: t for cid, t in (await runtime.tg_known()).items() if cid not in chats}
    return texts.tg_chats_screen(line, offered=len(offered)), tg_chats_kb(chats, offered)


async def _check_bot_access(bot: Bot, chat_id: int, fallback: str) -> tuple[str | None, str]:
    """Может ли бот писать в чат. Возвращает (ошибка или None, название)."""
    try:
        chat = await bot.get_chat(chat_id)
    except TelegramAPIError as exc:
        return texts.TG_CHAT_NO_ACCESS.format(error=escape(exc.message)), fallback
    title = chat.title or fallback or str(chat_id)
    if chat.type == ChatType.CHANNEL:
        try:
            member = await bot.get_chat_member(chat_id, bot.id)
        except TelegramAPIError as exc:
            return texts.TG_CHAT_NO_ACCESS.format(error=escape(exc.message)), title
        can_post = member.status == ChatMemberStatus.CREATOR or (
            isinstance(member, ChatMemberAdministrator) and bool(member.can_post_messages)
        )
        if not can_post:
            return texts.TG_CHAT_NOT_ADMIN.format(title=escape(title)), title
    return None, title


async def on_chat_shared(
    message: Message,
    bot: Bot,
    senders: Senders,
    runtime: RuntimeConfig,
    screens: Screens,
) -> None:
    shared = message.chat_shared
    assert shared is not None
    chat_id = message.chat.id
    error, title = await _check_bot_access(bot, shared.chat_id, shared.title or "")
    if error is not None:
        await screens.send(bot, chat_id, "pick", error, tg_pick_kb())
        return
    new = await runtime.add_tg_chat(str(shared.chat_id), title)
    count = len(await runtime.tg_chats())
    template = texts.TG_CHAT_ADDED if new else texts.TG_CHAT_ALREADY
    # Ответ возвращает нижнее меню вместо клавиатуры выбора, панель обновляется на месте.
    await screens.send(
        bot, chat_id, "pick", template.format(title=escape(title), count=count), main_menu_kb()
    )
    line = await service.tg_line(senders, runtime)
    await screens.edit(bot, chat_id, *await _tg_screen(line, runtime))


# --- бота добавили в чат Telegram / убрали из него ---

_PRESENT = {ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}


async def on_my_chat_member(
    event: ChatMemberUpdated, bot: Bot, runtime: RuntimeConfig, settings: Settings
) -> None:
    """Запоминаем группы и каналы, куда добавили бота (кем угодно), и пишем админам."""
    chat = event.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL):
        return
    cid, title = str(chat.id), chat.title or str(chat.id)
    if event.new_chat_member.status not in _PRESENT:
        await runtime.forget_tg_chat(cid)
        log.info("Бота убрали из чата TG %s (%s)", cid, title)
        return
    new = await runtime.remember_tg_chat(cid, title)
    joined = event.old_chat_member.status not in _PRESENT
    log.info("Бот в чате TG %s (%s), статус %s", cid, title, event.new_chat_member.status)
    if not (new and joined) or cid in await runtime.tg_chats():
        return
    by = event.from_user.full_name if event.from_user else ""
    kind = texts.TG_KIND_CHANNEL if chat.type == ChatType.CHANNEL else texts.TG_KIND_GROUP
    for admin_id in settings.admin_ids:
        with contextlib.suppress(TelegramAPIError):
            await bot.send_message(
                chat_id=admin_id,
                text=texts.tg_bot_added(title, kind, by),
                reply_markup=tg_offer_kb(cid, title),
            )


# --- MAX: подключение токеном ---


async def awaiting_max_token(message: Message, pending: PendingInput) -> bool:
    """Фильтр: это сообщение — токен MAX, которого ждём от этого админа."""
    return message.from_user is not None and pending.waiting(message.from_user.id)


async def on_max_token(
    message: Message,
    bot: Bot,
    senders: Senders,
    runtime: RuntimeConfig,
    screens: Screens,
    pending: PendingInput,
    max_factory: MaxFactory | None = None,
) -> None:
    token = (message.text or "").strip()
    add_secret(token)  # не светить в логах, даже если токен не подойдёт
    # Сообщение с токеном не должно оставаться в переписке.
    with contextlib.suppress(TelegramAPIError):
        await message.delete()
    chat_id = message.chat.id
    if max_factory is None:
        await screens.edit(bot, chat_id, texts.MAX_NOT_CONNECTED, back_kb("max"))
        return
    sender = max_factory(token)
    health = await sender.health()
    if not health.ok:
        log.warning("Токен MAX из меню не подошёл: %s", health.detail)
        await screens.edit(
            bot, chat_id, service.error_text(texts.MAX_TOKEN_BAD, health.detail), back_kb("max")
        )
        return  # ждём следующий токен или «Назад»
    assert message.from_user is not None
    pending.clear(message.from_user.id)
    await runtime.set_max_token(token)
    senders.max = sender
    log.info("MAX подключён из меню: бот %s", health.detail)
    ok = texts.MAX_TOKEN_OK.format(name=escape(health.detail))
    line = await service.max_line(senders, runtime)
    await screens.edit(bot, chat_id, f"{texts.max_section(line)}\n\n{ok}", max_section_kb())
