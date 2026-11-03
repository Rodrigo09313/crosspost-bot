"""Протоколы зависимостей обработчиков."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol


class BroadcastQueue(Protocol):
    """То, что обработчикам нужно от воркера."""

    @property
    def stopped_ids(self) -> set[int]: ...

    def wake(self) -> None: ...

    async def stop(self, ids: Collection[int] | None = None) -> list[int]: ...


class WaTitlesProvider(Protocol):
    """Возвращает {id группы: название}; при ошибке — пустой словарь."""

    async def __call__(self) -> dict[str, str]: ...
