"""Фоновое слушание MAX: бот сам запоминает чаты, куда его добавили или где пишут.

В MAX нет метода «список чатов бота», а события GET /updates отдаются один раз. Поэтому
бот всё время держит long polling и сразу сохраняет каждый замеченный чат в SQLite —
экран «💬 Чаты» берёт список оттуда, ничего не теряется между нажатиями «Обновить».
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.runtime import RuntimeConfig
from app.senders.base import SenderError
from app.senders.factory import Senders
from app.senders.max import MaxSender

log = logging.getLogger(__name__)


class MaxChatWatcher:
    def __init__(
        self,
        senders: Senders,
        runtime: RuntimeConfig,
        *,
        poll_timeout: int = 20,  # long polling MAX, с; меньше read-таймаута клиента (30 с)
        idle_delay: float = 10.0,  # MAX не подключён — проверить снова через
        error_backoff: float = 30.0,  # пауза после ошибки запроса
    ) -> None:
        self._senders = senders
        self._runtime = runtime
        self._poll_timeout = poll_timeout
        self._idle_delay = idle_delay
        self._error_backoff = error_backoff
        self._sender: MaxSender | None = None
        self._marker: int | None = None
        self._task: asyncio.Task[None] | None = None

    async def step(self) -> float:
        """Один опрос MAX. Возвращает, сколько подождать перед следующим (0 — сразу)."""
        sender = self._senders.max
        if sender is None:
            return self._idle_delay
        if sender is not self._sender:  # подключили другой токен — начать с чистого маркера
            self._sender, self._marker = sender, None
        try:
            events = await sender.poll_chats(self._marker, self._poll_timeout)
        except SenderError as exc:
            log.warning("MAX: не удалось получить события: %s", exc)
            return self._error_backoff
        self._marker = events.marker
        if events.seen or events.removed:
            await self._runtime.remember_max_chats(events.seen, events.removed)
            log.info(
                "MAX: замечены чаты %s, удалён из %s", sorted(events.seen), sorted(events.removed)
            )
        return 0.0

    async def run(self) -> None:
        log.info("Слушаю события MAX")
        while True:
            try:
                delay = await self.step()
            except Exception:
                log.exception("MAX: сбой фонового слушания")
                delay = self._error_backoff
            if delay:
                await asyncio.sleep(delay)

    def start(self) -> asyncio.Task[None]:
        self._task = asyncio.create_task(self.run(), name="max-chat-watcher")
        return self._task

    async def shutdown(self) -> None:
        """Опрос может висеть до poll_timeout — прерываем, а не ждём."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
