"""Тексты сервисных экранов: статус площадок, история рассылок, QR WhatsApp."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from html import escape

from aiogram.types import InputRichMessage

from app import texts
from app.runtime import RuntimeConfig
from app.senders.factory import Senders
from app.storage import HISTORY_KEEP_DAYS, Storage

MESSAGE_LIMIT = 4000
HISTORY_SHOW = 15  # строк в «Истории»


def chunk_lines(lines: Iterable[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Склеивает строки в сообщения не длиннее limit."""
    messages: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


# --- проверки площадок (для статуса и экранов площадок) ---


async def tg_rows(senders: Senders, runtime: RuntimeConfig) -> list[tuple[str, bool, str]]:
    """(название, есть доступ, ошибка) по каждому чату Telegram; названия обновляет в БД."""
    chats = await runtime.tg_chats()
    checks = await asyncio.gather(*(senders.tg.check_chat(cid) for cid in chats))
    rows: list[tuple[str, bool, str]] = []
    fresh: dict[str, str] = {}
    for (cid, title), check in zip(chats.items(), checks, strict=True):
        if check.ok:
            fresh[cid] = check.detail
            rows.append((check.detail, True, ""))
        else:
            rows.append((title, False, check.detail))
    await runtime.update_tg_titles(fresh)
    return rows


async def max_check(senders: Senders, runtime: RuntimeConfig) -> texts.Platform:
    if senders.max is None:
        return texts.Platform(connected=False, ok=False, detail="", chats=0)
    health = await senders.max.health()
    return texts.Platform(
        connected=True, ok=health.ok, detail=health.detail, chats=len(await runtime.max_chats())
    )


async def wa_check(senders: Senders, runtime: RuntimeConfig) -> texts.Platform:
    if senders.wa is None:
        return texts.Platform(connected=False, ok=False, detail="", chats=0)
    health = await senders.wa.health()
    return texts.Platform(
        connected=True,
        ok=health.ok,
        detail=health.detail,
        chats=len(await runtime.wa_group_ids()),
    )


async def tg_line(senders: Senders, runtime: RuntimeConfig) -> str:
    return texts.tg_platform(await tg_rows(senders, runtime))


async def max_line(senders: Senders, runtime: RuntimeConfig) -> str:
    p = await max_check(senders, runtime)
    return texts.max_platform(connected=p.connected, ok=p.ok, detail=p.detail, chats=p.chats)


async def wa_line(senders: Senders, runtime: RuntimeConfig) -> str:
    p = await wa_check(senders, runtime)
    return texts.wa_platform(
        connected=p.connected,
        ok=p.ok,
        detail=p.detail,
        groups=p.chats,
        delay=(await runtime.delay()).label if p.connected else "",
    )


# --- экраны ---


async def status_view(db: Storage, senders: Senders, runtime: RuntimeConfig) -> InputRichMessage:
    tg, max_, wa = await asyncio.gather(
        tg_rows(senders, runtime), max_check(senders, runtime), wa_check(senders, runtime)
    )
    active: list[tuple[int, str, str, dict[str, dict[str, int]]]] = [
        (b.id, b.text, b.status, await db.network_counts(b.id)) for b in await db.unfinished()
    ]
    return texts.status_rich(
        tg=tg, max_=max_, wa=wa, delay=(await runtime.delay()).label, active=active
    )


async def history_view(db: Storage) -> InputRichMessage | str:
    recent = await db.recent(HISTORY_SHOW)
    if not recent:
        return texts.HISTORY_EMPTY
    return texts.history_rich([(b.created_at, counts) for b, counts in recent], HISTORY_KEEP_DAYS)


def error_text(template: str, error: object) -> str:
    return template.format(error=escape(str(error)))
