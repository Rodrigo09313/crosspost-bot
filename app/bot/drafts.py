"""Черновики постов (в памяти) и подготовка рассылки."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import count

from app.config import NETWORK_MAX, NETWORK_TG, NETWORK_WA, Settings
from app.runtime import DelayPreset, Targets
from app.splitter import split_text
from app.storage import DeliveryPlan

WA_TYPING_AVG = 3.5  # средняя длительность «печатает», с


@dataclass(slots=True)
class Draft:
    id: int
    admin_id: int
    chat_id: int
    message_id: int
    text: str
    networks: set[str] = field(default_factory=set)


class DraftStore:
    """Черновики живут только в памяти: до подтверждения в БД ничего не пишется."""

    def __init__(self, max_size: int = 50) -> None:
        self._drafts: dict[int, Draft] = {}
        self._ids = count(1)
        self._max_size = max_size

    def add(
        self, *, admin_id: int, chat_id: int, message_id: int, text: str, networks: set[str]
    ) -> Draft:
        draft = Draft(next(self._ids), admin_id, chat_id, message_id, text, set(networks))
        self._drafts[draft.id] = draft
        while len(self._drafts) > self._max_size:
            self._drafts.pop(next(iter(self._drafts)))
        return draft

    def get(self, draft_id: int, admin_id: int) -> Draft | None:
        draft = self._drafts.get(draft_id)
        return draft if draft and draft.admin_id == admin_id else None

    def pop(self, draft_id: int) -> None:
        self._drafts.pop(draft_id, None)


def available_networks(targets: Targets) -> list[str]:
    """Сети, у которых выбрана хотя бы одна цель (MAX — ещё и подключён)."""
    nets: list[str] = []
    if targets.tg:
        nets.append(NETWORK_TG)
    if targets.max:
        nets.append(NETWORK_MAX)
    if targets.wa:
        nets.append(NETWORK_WA)
    return nets


def network_counts(targets: Targets) -> dict[str, int]:
    """Сколько чатов/групп в каждой сети."""
    return {NETWORK_TG: len(targets.tg), NETWORK_MAX: len(targets.max), NETWORK_WA: len(targets.wa)}


def target_counts(targets: Targets, networks: set[str]) -> dict[str, int]:
    counts = network_counts(targets)
    return {n: counts[n] for n in available_networks(targets) if n in networks}


def estimate_minutes(settings: Settings, counts: dict[str, int], delay: DelayPreset) -> int:
    """Сети идут параллельно, поэтому время — по самой длинной очереди.

    counts: сколько чатов в каждой сети рассылки. Пауза — между чатами, отправка ~1 с,
    в WhatsApp ещё «печатает».
    """
    longest = 0.0
    for net, n in counts.items():
        if n <= 0:
            continue
        send = WA_TYPING_AVG if net == NETWORK_WA and settings.wa_typing else 1.0
        longest = max(longest, (n - 1) * delay.avg_s + n * send)
    return max(1, math.ceil((5.0 + longest) / 60))


def build_plans(targets: Targets, networks: set[str], text: str) -> list[DeliveryPlan]:
    """Строки доставок: каждый выбранный чат TG и MAX (MAX — по частям), каждая группа WA."""
    plans: list[DeliveryPlan] = []
    if NETWORK_TG in networks:
        for cid, title in targets.tg.items():
            plans.append(DeliveryPlan(NETWORK_TG, cid, title or cid))
    if NETWORK_MAX in networks:
        parts = len(split_text(text))
        for cid, title in targets.max.items():
            for no in range(1, parts + 1):
                plans.append(DeliveryPlan(NETWORK_MAX, cid, title or cid, no))
    if NETWORK_WA in networks:
        for gid, title in targets.wa.items():
            plans.append(DeliveryPlan(NETWORK_WA, gid, title or gid))
    return plans


class AlbumTracker:
    """Помнит последние media_group_id, чтобы отвечать на альбом один раз."""

    def __init__(self, max_size: int = 100) -> None:
        self._seen: dict[str, None] = {}
        self._max_size = max_size

    def first_seen(self, media_group_id: str) -> bool:
        if media_group_id in self._seen:
            return False
        self._seen[media_group_id] = None
        while len(self._seen) > self._max_size:
            self._seen.pop(next(iter(self._seen)))
        return True
