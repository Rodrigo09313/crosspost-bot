"""Все строки интерфейса бота (русский язык).

Тексты с HTML-разметкой (parse_mode=HTML); пользовательские данные экранируются
через html.escape в вызывающем коде или в функциях ниже.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape

from aiogram.types import InputRichBlockUnion, InputRichMessage

from app import rich

NETWORK_NAMES = {"tg": "Telegram", "max": "MAX", "wa": "WhatsApp"}
NETWORK_SHORT = {"tg": "TG", "max": "MAX", "wa": "WA"}

# --- меню (постоянная клавиатура внизу чата) ---
BTN_MENU_POST = "📝 Рассылка"
BTN_MENU_STOP = "⏹ Стоп"
BTN_MENU_TG = "✈ Telegram"
BTN_MENU_WA = "💬 WhatsApp"
BTN_MENU_MAX = "🟣 MAX"
BTN_MENU_SETTINGS = "⚙ Настройки"
BTN_MENU_HELP = "❓ Помощь"
# Подписи кнопок прошлых версий меню: у админов в клиенте может остаться старая клавиатура.
LEGACY_POST = "📝 Новый пост"
LEGACY_STATUS = "📊 Статус"
LEGACY_TG_CHATS = "📢 Чаты Telegram"
LEGACY_WA_GROUPS = "👥 Группы WhatsApp"
LEGACY_DELAY = "⏱ Паузы"
LEGACY_WA_CONNECT = "📱 Подключить WhatsApp"
LEGACY_HISTORY = "🗂 История"
# Список команд для синей кнопки «Меню» у поля ввода (set_my_commands).
BOT_COMMANDS = (
    ("start", "Показать меню"),
    ("tg", "Telegram: каналы и группы"),
    ("wa", "WhatsApp: группы и подключение"),
    ("max", "MAX: чаты и подключение"),
    ("status", "Статус рассылки и площадок"),
    ("pause", "Пауза между чатами"),
    ("history", "Последние рассылки"),
    ("stop", "Остановить рассылку"),
)
MENU_PLACEHOLDER = "Пришлите текст поста или выберите действие"

START = (
    "👋 <b>Бот мультирассылки</b>\n\n"
    "Пришлите мне текст поста — я покажу предпросмотр и разошлю его "
    "в Telegram, MAX и группы WhatsApp.\n\n"
    "Все действия — в меню внизу. Что делает каждая кнопка — «❓ Помощь»."
)
HELP = (
    "❓ <b>Как пользоваться ботом</b>\n\n"
    "<b>📝 Рассылка</b> — как отправить пост. Коротко: просто пришлите текст сообщением, "
    "бот покажет предпросмотр и кнопки площадок.\n\n"
    "<b>⏹ Стоп</b> — список идущих и ждущих рассылок: остановить одну (остальные "
    "продолжат) или все, с подтверждением. Под сообщением о ходе рассылки тоже есть "
    "«⏹ Остановить». Уже отправленное не отзывается, оставшиеся чаты пропускаются.\n\n"
    "<b>✈ Telegram</b> — каналы и группы Telegram для рассылки. «➕ Добавить» открывает "
    "список ваших каналов и групп, где вы админ: выберите нужный, бот добавится туда сам. "
    "Нажмите на чат в списке, чтобы убрать его.\n\n"
    "<b>💬 WhatsApp</b> — «👥 Группы»: галочкой ✅ отмечаете, куда рассылать; "
    "«📱 Подключить»: QR-код для привязки рабочего номера.\n\n"
    "<b>🟣 MAX</b> — «🔑 Подключить»: пришлите токен бота MAX; «💬 Чаты»: галочкой ✅ "
    "отмечаете чаты и каналы MAX, куда рассылать.\n\n"
    "<b>⚙ Настройки</b> — «📊 Статус» (идёт ли рассылка, всё ли подключено), "
    "«⏱ Паузы» между чатами (одна на все сети), «🗂 История» последних рассылок.\n\n"
    "Разделы открываются в одном сообщении: кнопки меняют его, «↩ Назад» — шаг назад.\n\n"
    "<b>Команды</b>: /tg, /wa, /max, /status, /pause, /history, /stop; "
    "/here — в группе Telegram показать её ID."
)
POST_HOWTO = (
    "📝 <b>Рассылка</b>\n\n"
    "1. Пришлите текст поста следующим сообщением (до 4096 символов, только текст, "
    "эмодзи можно).\n"
    "2. Бот покажет предпросмотр и кнопки площадок: ✅ — отправлять, ⬜ — нет.\n"
    "3. «🚀 Отправить» → проверьте, куда и сколько по времени → «Да».\n"
    "4. Бот пришлёт прогресс и итоговый отчёт: куда дошло, где ошибка.\n\n"
    "Пересланный пост из канала бот не рассылает, а показывает ID канала — "
    "текст для рассылки вставляйте, а не пересылайте."
)

# --- приём поста ---
ONLY_TEXT = (
    "⚠️ В MVP только текст.\n"
    "Фото, видео, документы и альбомы не поддерживаются — пришлите пост обычным текстом."
)
TOO_LONG = "⚠️ Текст длиннее {limit} символов. Сократите пост."
UNKNOWN_COMMAND = "Неизвестная команда. Список команд — /start"
NO_NETWORKS_CONFIGURED = (
    "⚠️ Не выбрана ни одна площадка. Выберите чаты в разделах меню «✈ Telegram», "
    "«🟣 MAX» и «💬 WhatsApp»."
)
CHANNEL_ID = "ID канала «{title}»: <code>{chat_id}</code>"
HERE_ID = "ID этой группы: <code>{chat_id}</code>"

BTN_SEND = "🚀 Отправить"
BTN_CANCEL = "✖ Отмена"
BTN_YES = "Да"
BTN_NO = "Нет"
BTN_ON = "✅"
BTN_OFF = "⬜"

DRAFT_EXPIRED = "Черновик устарел. Пришлите пост ещё раз."
SELECT_NETWORK = "Выберите хотя бы одну площадку"
CANCELLED = "✖ Отменено."
QUEUED = "📥 Рассылка #{id} поставлена в очередь."
QUEUED_AHEAD = "📥 Рассылка #{id} поставлена в очередь. Перед ней: {ahead}."


def network_button(network: str, enabled: bool, count: int) -> str:
    """Кнопка площадки в предпросмотре; число чатов — у WhatsApp всегда, у остальных от 2."""
    mark = BTN_ON if enabled else BTN_OFF
    name = NETWORK_NAMES[network]
    if network == "wa" or count > 1:
        name = f"{name} ({count})"
    return f"{mark} {name}"


def preview(text: str, *, max_parts: int, limit: int = 300) -> str:
    snippet = text if len(text) <= limit else text[:limit].rstrip() + "…"
    return (
        "📝 <b>Предпросмотр</b>\n\n"
        f"<blockquote>{escape(snippet)}</blockquote>\n\n"
        f"Длина: {len(text)} симв. · Частей для MAX: {max_parts}\n"
        "Выберите площадки и нажмите «Отправить»."
    )


STATE_SHORT = {
    "queued": "в очереди",
    "running": "идёт",
    "paused": "на паузе",
    "done": "завершена",
    "stopped": "остановлена",
}


def confirm(
    counts: dict[str, int],
    minutes: int,
    *,
    ahead: int = 0,
    duplicates: list[tuple[int, str, str]] | None = None,
) -> str:
    """duplicates: (номер, когда, статус) рассылок с тем же текстом."""
    targets = ", ".join(f"{NETWORK_SHORT[n]} {c}" for n, c in counts.items())
    lines: list[str] = []
    if duplicates:
        lines.append("⚠️ <b>Этот пост уже рассылался:</b>")
        lines += [
            f"    #{bid} · {when_} · {STATE_SHORT.get(st, st)}" for bid, when_, st in duplicates
        ]
        lines += ["Повторная отправка в те же группы — дубли и риск жалоб на спам.", ""]
    lines += [f"Отправить в: {targets}?", f"Примерно {minutes} мин"]
    if ahead:
        lines.append(f"⏳ Перед ней в очереди рассылок: {ahead}")
    return "\n".join(lines)


# --- воркер: прогресс и отчёт ---
ICON_OK = "✅"
ICON_ERROR = "❌"
ICON_PENDING = "⏳"
ICON_SKIPPED = "⏹"

RESUMED = "🔄 Рассылка #{id} продолжена после перезапуска."
WA_PAUSED = (
    "⏸ Рассылка #{id}: WhatsApp-сессия не подключена ({detail}).\n"
    "WhatsApp-часть на паузе. Привяжите номер: /wa_qr — рассылка продолжится сама."
)
WA_RESUMED = "▶️ Рассылка #{id}: WhatsApp-сессия подключена, продолжаю."
NETWORK_NOT_CONFIGURED = "Площадка не настроена в .env"
STOPPED_BY_ADMIN = "Остановлено вручную («Стоп»)"
MAYBE_DELIVERED = (
    "ответ не получен, сообщение могло дойти — проверьте чат вручную "
    "(повтор не делали, чтобы не было дубля)"
)
ERROR_MESSAGE = (
    "⚠️ Не получилось выполнить действие: {reason}\n"
    "Попробуйте ещё раз. Если повторяется — проверьте «📊 Статус» и логи бота."
)
ERROR_ALERT = "⚠️ Ошибка. Подробности — в сообщении в чате."
STALE_BUTTON = "Кнопка устарела. Откройте раздел заново через меню внизу."
REFRESHING = "🔄 Обновляю список групп…"
BAD_RESPONSE = "неожиданный ответ сервера (не JSON)"
NOTHING_TO_STOP = "Сейчас нет активной рассылки."

# --- остановка рассылок ---
BTN_STOP_ALL = "⏹ Остановить все"
BTN_STOP_YES = "✅ Да, остановить"
BTN_STOP_ALL_YES = "✅ Да, остановить все"
BTN_STOP_PROGRESS = "⏹ Остановить"
STOP_DONE = "⏹ Рассылка #{id} остановлена"
STOP_ALL_DONE = "⏹ Остановлено рассылок: {n}"
STOP_GONE = "Рассылка #{id} уже завершена"
STOP_WAITING = "ждёт"


def stop_screen(count: int) -> str:
    title = "⏹ <b>Остановка рассылок</b>\n\n"
    if not count:
        return title + NOTHING_TO_STOP
    return (
        title + f"Идут и ждут: {count}. Нажмите на рассылку, чтобы остановить только её — "
        "остальные продолжат."
    )


def _done_total(counts: dict[str, int]) -> tuple[int, int]:
    total = sum(counts.values())
    return total - counts.get("pending", 0), total


def stop_state(counts: dict[str, dict[str, int]]) -> str:
    """Коротко для кнопки: сеть, где ещё идёт отправка («WA 45/217»), или «ждёт»."""
    if not any(_done_total(c)[0] for c in counts.values()):
        return STOP_WAITING
    for net in ("wa", "max", "tg"):
        c = counts.get(net)
        if c and c.get("pending"):
            done, total = _done_total(c)
            return f"{NETWORK_SHORT[net]} {done}/{total}"
    return STOP_WAITING


def stop_button(broadcast_id: int, title: str, state: str) -> str:
    return f"⏹ #{broadcast_id} · {title} · {state}"


def stop_confirm(broadcast_id: int, title: str, counts: dict[str, dict[str, int]]) -> str:
    sent = " · ".join(
        f"{NETWORK_SHORT[n]} {'/'.join(map(str, _done_total(counts[n])))}"
        for n in ("tg", "max", "wa")
        if n in counts
    )
    left = sum(c.get("pending", 0) for c in counts.values())
    return (
        f"⏹ <b>Остановить рассылку #{broadcast_id}?</b>\n«{escape(title)}»\n\n"
        f"Уже отправлено: {sent}\n"
        f"Ещё не отправлено: {left} — эти чаты пост не получат. Продолжить потом нельзя."
    )


def stop_all_confirm(count: int) -> str:
    return (
        f"⏹ <b>Остановить все рассылки ({count})?</b>\n\n"
        "Неотправленные чаты пост не получат. Продолжить потом нельзя."
    )


def _net_icon(statuses: list[str]) -> str:
    if not statuses:
        return ICON_OK
    if "pending" in statuses:
        return ICON_PENDING
    if "error" in statuses:
        return ICON_ERROR
    if "skipped" in statuses:
        return ICON_SKIPPED
    return ICON_OK


def progress(
    broadcast_id: int,
    networks: list[str],
    statuses: dict[str, list[str]],
    *,
    paused: bool = False,
) -> str:
    items: list[str] = []
    for net in ("tg", "max", "wa"):
        if net in networks:
            st = statuses.get(net, [])
            done = sum(1 for s in st if s != "pending")
            icon = _net_icon(st)
            items.append(
                f"{NETWORK_SHORT[net]} {done}/{len(st)}"
                + ("" if icon == ICON_PENDING else f" {icon}")
            )
    if paused:
        header = f"⏸ Рассылка #{broadcast_id} · WhatsApp на паузе"
    else:
        header = f"📤 Рассылка #{broadcast_id}"
    return f"{header}\n" + " · ".join(items)


def report(
    broadcast_id: int,
    networks: list[str],
    rows: list[tuple[str, str, str, str | None]],
    *,
    stopped: bool,
) -> str:
    """rows: (network, target_title, status, error)."""
    header = (
        f"{ICON_SKIPPED} Рассылка #{broadcast_id} остановлена"
        if stopped
        else f"🏁 Рассылка #{broadcast_id} завершена"
    )
    lines = [f"<b>{header}</b>", ""]
    for net in networks:
        net_rows = [r for r in rows if r[0] == net]
        statuses = [r[2] for r in net_rows]
        ok = statuses.count("ok")
        icon = _net_icon(statuses)
        if net == "max":
            chats = len({title for _, title, _, _ in net_rows})
            summary = f"{ok}/{len(net_rows)} част." + (f" · чатов: {chats}" if chats > 1 else "")
        elif net == "wa":
            summary = f"{ok}/{len(net_rows)} групп"
        elif net == "tg" and len(net_rows) > 1:
            summary = f"{ok}/{len(net_rows)} чатов"
        else:
            summary = ""
        lines.append(f"{icon} {NETWORK_NAMES[net]} {summary}".rstrip())
        for _, title, status, error in net_rows:
            if status == "error":
                lines.append(f"    {ICON_ERROR} {escape(title)} — {escape(error or '')}")
        skipped = statuses.count("skipped")
        if skipped:
            lines.append(f"    {ICON_SKIPPED} не отправлено: {skipped}")
    return "\n".join(lines)


# --- сервисные команды: статус и история ---
HISTORY_EMPTY = "Рассылок ещё не было."
NO_ACTIVE = "Сейчас рассылок нет."
ICON_OFF = "⚪"
ICON_WARN = "⚠️"


def when(dt: datetime, now: datetime | None = None) -> str:
    """«сегодня 19:52», «вчера 08:10» или «28.10 14:05» — в местном времени (TZ)."""
    local = dt.astimezone()
    today = (now or datetime.now().astimezone()).date()
    if local.date() == today:
        day = "сегодня"
    elif (today - local.date()).days == 1:
        day = "вчера"
    else:
        day = local.strftime("%d.%m")
    return f"{day} {local:%H:%M}"


def tg_platform(chats: list[tuple[str, bool, str]]) -> str:
    """chats: (название, есть доступ, ошибка) по каждому чату Telegram из рассылки."""
    if not chats:
        return f"{ICON_OFF} Telegram — чаты не выбраны («➕ Добавить» в разделе «{BTN_MENU_TG}»)"
    icon = ICON_ERROR if any(not ok for _, ok, _ in chats) else ICON_OK
    if len(chats) == 1:
        title, ok, error = chats[0]
        if ok:
            return f"{icon} Telegram — «{escape(title)}»"
        return f"{icon} Telegram — нет доступа к «{escape(title)}»: {escape(error)}"
    lines = [f"{icon} Telegram — чатов: {len(chats)}"]
    for title, ok, error in chats:
        if ok:
            lines.append(f"    {ICON_OK} «{escape(title)}»")
        else:
            lines.append(f"    {ICON_ERROR} «{escape(title)}» — нет доступа: {escape(error)}")
    return "\n".join(lines)


def max_platform(*, connected: bool, ok: bool, detail: str, chats: int) -> str:
    if not connected:
        return f"{ICON_OFF} MAX — не подключён (раздел «{BTN_MENU_MAX}» → «🔑 Подключить»)"
    if not ok:
        return f"{ICON_ERROR} MAX — ошибка: {escape(detail)}"
    chosen = f"чатов: {chats}" if chats else "чаты не выбраны («💬 Чаты»)"
    return f"{ICON_OK} MAX — бот {escape(detail)} · {chosen}"


def wa_platform(*, connected: bool, ok: bool, detail: str, groups: int, delay: str) -> str:
    if not connected:
        return f"{ICON_OFF} WhatsApp — не настроен"
    if not ok:
        return f"{ICON_ERROR} WhatsApp — не подключён ({escape(detail)}). Нажмите «📱 Подключить»"
    chosen = f"{groups} гр." if groups else "группы не выбраны («👥 Группы»)"
    return f"{ICON_OK} WhatsApp — подключён · {chosen} · пауза {delay}"


# --- статус и история: rich-таблицы ---
STATUS_TITLE = "📊 Статус"
STATUS_PLATFORMS = "Площадки"
STATUS_ACTIVE = "Идущие рассылки"
HISTORY_TITLE = "🗂 История"
HISTORY_KEEP = "Бот хранит рассылки {days} дней."
PLATFORMS_HEAD = ["Сеть", "Состояние", "Чатов", "Пауза"]
ACTIVE_HEAD = ["№", "Пост", "TG", "MAX", "WA"]
HISTORY_HEAD = ["Дата", "TG", "MAX", "WA"]
CELL_NONE = "—"
CELL_WA_PAUSED = "⏸ пауза"
PROBLEMS = "⚠️ Проблемы: {n}"


@dataclass(frozen=True, slots=True)
class Platform:
    """Проверка площадки для статуса."""

    connected: bool  # настроена/подключена в боте
    ok: bool  # отвечает
    detail: str  # имя бота или текст ошибки
    chats: int  # выбрано чатов/групп


def post_title(text: str, limit: int = 24) -> str:
    """Первая непустая строка поста, обрезанная, — чтобы узнать пост в таблице."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def progress_cell(counts: dict[str, int] | None, *, paused: bool = False) -> str:
    """Ячейка сети: «45/195 ⏳», «195/195 ✅», «190/195 ❌», «—» если сеть не выбрана."""
    if not counts:
        return CELL_NONE
    total = sum(counts.values())
    ok = counts.get("ok", 0)
    if counts.get("pending"):
        return CELL_WA_PAUSED if paused else f"{ok}/{total} {ICON_PENDING}"
    if ok == total:
        icon = ICON_OK
    elif counts.get("error"):
        icon = ICON_ERROR
    else:
        icon = ICON_SKIPPED
    return f"{ok}/{total} {icon}"


def _platform_rows(
    tg: list[tuple[str, bool, str]], max_: Platform, wa: Platform, delay: str
) -> tuple[list[list[str]], list[str]]:
    """Строки таблицы «Площадки» и список проблем."""
    problems: list[str] = []
    bad = [(title, error) for title, ok, error in tg if not ok]
    if not tg:
        tg_state = f"{ICON_OFF} чаты не выбраны"
        problems.append(f"{ICON_OFF} Telegram — чаты не выбраны: «{BTN_MENU_TG}» → «➕ Добавить»")
    elif bad:
        tg_state = f"{ICON_ERROR} нет доступа: {len(bad)}"
        problems += [f"{ICON_ERROR} Telegram «{t}» — нет доступа: {e}" for t, e in bad]
    else:
        tg_state = f"{ICON_OK} доступ есть"

    if not max_.connected:
        max_state = f"{ICON_OFF} не подключён"
        problems.append(f"{ICON_OFF} MAX — не подключён: «{BTN_MENU_MAX}» → «🔑 Подключить»")
    elif not max_.ok:
        max_state = f"{ICON_ERROR} ошибка"
        problems.append(f"{ICON_ERROR} MAX — {max_.detail}")
    else:
        max_state = f"{ICON_OK} подключён"
        if not max_.chats:
            problems.append(f"{ICON_OFF} MAX — чаты не выбраны: «{BTN_MENU_MAX}» → «💬 Чаты»")

    if not wa.connected:
        wa_state = f"{ICON_OFF} не настроен"
        problems.append(f"{ICON_OFF} WhatsApp — не настроен в .env")
    elif not wa.ok:
        wa_state = f"{ICON_ERROR} не подключён"
        problems.append(
            f"{ICON_ERROR} WhatsApp — не подключён ({wa.detail}): «{BTN_MENU_WA}» → «📱 Подключить»"
        )
    else:
        wa_state = f"{ICON_OK} подключён"
        if not wa.chats:
            problems.append(
                f"{ICON_OFF} WhatsApp — группы не выбраны: «{BTN_MENU_WA}» → «👥 Группы»"
            )

    rows = [
        ["Telegram", tg_state, str(len(tg)), delay],
        ["MAX", max_state, str(max_.chats) if max_.connected else CELL_NONE, delay],
        ["WhatsApp", wa_state, str(wa.chats) if wa.connected else CELL_NONE, delay],
    ]
    return rows, problems


def status_rich(
    *,
    tg: list[tuple[str, bool, str]],
    max_: Platform,
    wa: Platform,
    delay: str,
    active: list[tuple[int, str, str, dict[str, dict[str, int]]]],
) -> InputRichMessage:
    """tg: (название, есть доступ, ошибка) по чатам; active: (№, текст, статус, счётчики)."""
    rows, problems = _platform_rows(tg, max_, wa, delay)
    blocks: list[InputRichBlockUnion] = [
        rich.heading(STATUS_TITLE, 2),
        rich.heading(STATUS_PLATFORMS, 4),
        rich.table(PLATFORMS_HEAD, rows),
    ]
    if problems:
        blocks.append(rich.details(PROBLEMS.format(n=len(problems)), problems))
    blocks.append(rich.heading(STATUS_ACTIVE, 4))
    if active:
        blocks.append(
            rich.table(
                ACTIVE_HEAD,
                [
                    [
                        str(bid),
                        post_title(text),
                        progress_cell(counts.get("tg")),
                        progress_cell(counts.get("max")),
                        progress_cell(counts.get("wa"), paused=status == "paused"),
                    ]
                    for bid, text, status, counts in active
                ],
            )
        )
    else:
        blocks.append(rich.paragraph(NO_ACTIVE))
    return rich.message(blocks)


def history_rich(
    entries: list[tuple[datetime, dict[str, dict[str, int]]]], keep_days: int
) -> InputRichMessage:
    """entries: (когда создана, счётчики по сетям) — новые первыми."""
    rows = [
        [
            when(created),
            progress_cell(counts.get("tg")),
            progress_cell(counts.get("max")),
            progress_cell(counts.get("wa")),
        ]
        for created, counts in entries
    ]
    return rich.message(
        [
            rich.heading(HISTORY_TITLE, 2),
            rich.table(HISTORY_HEAD, rows),
            rich.paragraph(HISTORY_KEEP.format(days=keep_days)),
        ]
    )


WA_NOT_CONFIGURED = (
    "WhatsApp не настроен: проверьте WA_PROVIDER и ключи Evolution API / GREEN-API в .env."
)
WA_ALREADY_CONNECTED = "✅ WhatsApp уже подключён, QR не нужен."
WA_QR_CAPTION = (
    "Отсканируйте QR на телефоне с рабочим номером:\n"
    "WhatsApp → Связанные устройства → Привязка устройства.\n"
    "Код живёт около минуты; если не успели — «🔄 Новый QR»."
)
WA_QR_ERROR = "Не удалось получить QR-код: {error}"
WA_GROUPS_EMPTY = "Номер не состоит ни в одной группе WhatsApp."
WA_GROUPS_ERROR = (
    "⚠️ Не удалось получить список групп: {error}\n"
    "Показаны только уже выбранные. Проверьте подключение: «📱 Подключить»."
)

# --- разделы меню (одно сообщение, кнопки меняют его) ---
BTN_BACK = "↩ Назад"
BTN_STATUS = "📊 Статус"
BTN_DELAY = "⏱ Паузы"
BTN_HISTORY = "🗂 История"
BTN_WA_GROUPS = "👥 Группы"
BTN_WA_CONNECT = "📱 Подключить"
BTN_WA_NEW_QR = "🔄 Новый QR"
BTN_MAX_CHATS = "💬 Чаты"
BTN_MAX_CONNECT = "🔑 Подключить"


def tg_section(line: str) -> str:
    return f"✈ <b>Telegram</b>\n\n{line}"


def wa_section(line: str) -> str:
    return f"💬 <b>WhatsApp</b>\n\n{line}"


def max_section(line: str) -> str:
    return f"🟣 <b>MAX</b>\n\n{line}"


SETTINGS = "⚙ <b>Настройки</b>\n\nСтатус площадок, паузы между чатами, история."

# --- MAX: подключение и чаты ---
MAX_TOKEN_ASK = (
    "🔑 <b>Подключение MAX</b>\n\n"
    "Пришлите следующим сообщением токен бота MAX "
    "(платформа MAX для партнёров → ваш бот → «Токен»).\n"
    "Бот сразу удалит сообщение с токеном из чата и проверит его."
)
MAX_TOKEN_OK = "✅ MAX подключён: бот {name}. Теперь выберите чаты — «💬 Чаты»."
MAX_TOKEN_BAD = (
    "❌ Токен не подошёл: {error}\n"
    "Прежнее подключение не менялось. Пришлите другой токен или нажмите «↩ Назад»."
)
MAX_NOT_CONNECTED = "MAX не подключён — сначала «🔑 Подключить»."
MAX_CHATS_EMPTY = (
    "Бот MAX пока не видит ни одного чата. Добавьте бота в группу или канал MAX "
    "(в канал — админом) и напишите там любое сообщение (в канале — пост): бот "
    "заметит чат сам. Затем нажмите «🔄 Обновить»."
)
MAX_CHATS_ERROR = "⚠️ Не удалось получить чаты MAX: {error}\nПоказаны только уже выбранные."
NOT_IN_CHAT = "бот не видит чат"


def max_chats_screen(*, selected: int, total: int, error: str | None, empty: bool) -> str:
    lines = [
        "💬 <b>Чаты MAX для рассылки</b>",
        "",
        "Нажмите на чат, чтобы включить ✅ или выключить ⬜ его. Сохраняется сразу.",
        "",
    ]
    if error is not None:
        lines += [MAX_CHATS_ERROR.format(error=escape(error)), ""]
    elif empty:
        lines += [MAX_CHATS_EMPTY, ""]
    lines.append(f"Выбрано: {selected} из {total}")
    return "\n".join(lines)


def max_chat_button(title: str, selected: bool, member: bool) -> str:
    mark = BTN_ON if selected else BTN_OFF
    suffix = "" if member else f" ({NOT_IN_CHAT})"
    return f"{mark} {title or 'без названия'}{suffix}"


# --- меню: группы WhatsApp ---
WA_CONNECT_HOWTO = (
    "📱 <b>Подключение WhatsApp</b>\n\n"
    "Бот отправляет в WhatsApp от имени обычного номера — так же, как WhatsApp Web.\n"
    "1. Возьмите телефон с рабочим номером (не личным).\n"
    "2. Ниже придёт QR-код. На телефоне: WhatsApp → «Связанные устройства» → "
    "«Привязка устройства» → отсканируйте.\n"
    "3. Нажмите «↩ Назад»: у WhatsApp должно быть «подключён».\n\n"
    "Телефон должен выходить в интернет хотя бы раз в 1–2 недели, иначе привязка слетит."
)
BTN_PREV = "◀"
BTN_NEXT = "▶"
BTN_REFRESH = "🔄 Обновить"
NOT_MEMBER = "номер не в группе"


WA_GROUPS_LOADING = (
    "⏳ <b>Загружаю список групп WhatsApp…</b>\n\n"
    "Если групп у номера много, это занимает до 2 минут. Список сохранится — "
    "в следующий раз откроется сразу."
)


def wa_groups_screen(
    *,
    selected: int,
    total: int,
    error: str | None,
    empty: bool,
    updated: datetime | None = None,
) -> str:
    lines = [
        "👥 <b>Группы WhatsApp для рассылки</b>",
        "",
        "Нажмите на группу, чтобы включить ✅ или выключить ⬜ её. Сохраняется сразу.",
        "Здесь группы, где состоит подключённый номер. Нет нужной — добавьте номер "
        "в группу и нажмите «🔄 Обновить».",
        "",
    ]
    if error is not None:
        lines += [WA_GROUPS_ERROR.format(error=escape(error)), ""]
    elif empty:
        lines += [WA_GROUPS_EMPTY, ""]
    lines.append(f"Выбрано: {selected} из {total}")
    if updated is not None:
        lines.append(f"Список групп от {when(updated)}")
    return "\n".join(lines)


def wa_group_button(title: str, selected: bool, member: bool) -> str:
    mark = BTN_ON if selected else BTN_OFF
    suffix = "" if member else f" ({NOT_MEMBER})"
    return f"{mark} {title or 'без названия'}{suffix}"


# --- меню: чаты Telegram ---
BTN_TG_ADD = "➕ Добавить"
BTN_TG_REMOVE = "🗑 Убрать"
BTN_TG_PICK_CHANNEL = "📢 Выбрать канал"
BTN_TG_PICK_GROUP = "👥 Выбрать группу"
BTN_TG_BACK = "↩ Назад в меню"
TG_PICK_PLACEHOLDER = "Выберите канал или группу кнопкой ниже"
TG_PICK_HOWTO = (
    "➕ <b>Добавить чат Telegram</b>\n\n"
    "Нажмите кнопку внизу — Telegram покажет ваши каналы или группы. "
    "Выберите нужный и подтвердите: бот добавится туда сам.\n\n"
    "• <b>Канал</b> — в списке только каналы, где вы админ с правом публикации; "
    "бот станет админом с правом публиковать посты.\n"
    "• <b>Группа</b> — бот станет участником группы.\n\n"
    "Закончили — «↩ Назад в меню»."
)
TG_BACK = "Главное меню."
TG_CHAT_ADDED = "✅ «{title}» добавлен в рассылку. Чатов Telegram: {count}."
TG_CHAT_ALREADY = "«{title}» уже в рассылке. Чатов Telegram: {count}."
TG_CHAT_NO_ACCESS = (
    "⚠️ Бот не видит этот чат: {error}\n"
    "Добавьте бота в чат вручную (в канал — админом с правом публикации) и выберите ещё раз."
)
TG_CHAT_NOT_ADMIN = (
    "⚠️ В канале «{title}» бот не админ с правом публикации. "
    "Сделайте его админом (право «Публикация сообщений») и выберите канал ещё раз."
)
TG_CHAT_REMOVED = "🗑 «{title}» убран из рассылки."


def tg_chats_screen(line: str, *, offered: int = 0) -> str:
    text = (
        f"{tg_section(line)}\n\n"
        "Пост уходит во все чаты из списка. «➕ Добавить» — выбрать канал или группу "
        "из тех, где вы админ; нажмите на чат, чтобы убрать его."
    )
    if offered:
        text += (
            "\n\n«➕ …» внизу — чаты, куда бота добавили (в том числе другие админы): "
            "нажмите, чтобы включить в рассылку."
        )
    return text


def tg_offer_button(title: str) -> str:
    return f"➕ {title or 'без названия'}"


def tg_bot_added(title: str, kind: str, by: str) -> str:
    return (
        f"🔔 Бота добавили в {kind} «{escape(title)}»"
        + (f", добавил: {escape(by)}" if by else "")
        + ".\nЧтобы рассылать туда, нажмите кнопку ниже или «➕ …» в разделе «✈ Telegram»."
    )


TG_KIND_CHANNEL = "канал"
TG_KIND_GROUP = "группу"


def tg_chat_button(title: str) -> str:
    return f"📢 {title or 'без названия'}"


def tg_remove_ask(title: str) -> str:
    return f"Убрать «{escape(title)}» из рассылки?\nСам бот из чата не выйдет."


# --- меню: паузы ---
DELAY_HINTS = {
    "5-15s": "как руками, только прогретые номера",
    "10-30s": "рекомендуется",
    "20-60s": "осторожнее",
    "1-3m": "очень осторожно, долго",
}
DELAY_SET = "Пауза: {label}"


def delay_screen(*, current: str, counts: dict[str, int], minutes: int) -> str:
    """counts: сколько чатов выбрано в каждой сети."""
    chats = ", ".join(f"{NETWORK_SHORT[n]} {c}" for n, c in counts.items() if c)
    estimate = (
        f"Рассылка на все чаты ({chats}) займёт примерно {minutes} мин."
        if chats
        else "Чаты для рассылки не выбраны."
    )
    return (
        "⏱ <b>Пауза между чатами</b>\n\n"
        "Одна на все сети: Telegram, MAX и WhatsApp. У каждой сети своя очередь, сети "
        "идут параллельно. После каждого чата бот ждёт случайное время из диапазона, чтобы "
        "рассылка выглядела как ручная: ровные интервалы распознают как бота.\n\n"
        f"Сейчас: <b>{current}</b>. {estimate}\n"
        "Новый режим действует сразу, в том числе для идущей рассылки — со следующего чата."
    )


def delay_button(label: str, hint: str, selected: bool) -> str:
    mark = BTN_ON if selected else BTN_OFF
    return f"{mark} {label} — {hint}"
