"""Воркер рассылок: очереди по сетям, паузы, повторы, прогресс, отчёт, «Стоп», возобновление.

У каждой сети (Telegram, MAX, WhatsApp) своя очередь-дорожка. Дорожки идут параллельно
и друг друга не ждут: Telegram может уже рассылать пост №2, пока WhatsApp заканчивает №1.
Внутри дорожки — по одному чату, рассылки по порядку, между отправками случайная пауза
из настроек меню (одна на все сети). Части одного поста в MAX идут с короткой паузой.

Каждую доставку отправляет только дорожка её сети, поэтому дублей нет. Завершение
(статус и отчёт) — под замком, когда ни одна дорожка рассылкой больше не занята:
отчёт приходит один раз.

Источник истины — SQLite. Строки доставок создаются заранее со статусом pending,
итог каждой отправки пишется сразу после неё. Поэтому после перезапуска дорожки
просто продолжают с оставшихся pending — без дублей и пропусков.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from app import texts
from app.config import NETWORK_MAX, NETWORK_TG, NETWORK_WA
from app.senders.base import Sender, SourceMessage
from app.splitter import split_text
from app.storage import HISTORY_KEEP_DAYS, Broadcast, Delivery, Storage

log = logging.getLogger(__name__)

LANES = (NETWORK_TG, NETWORK_MAX, NETWORK_WA)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int  # всего попыток, включая первую
    delay: float  # пауза перед повтором, с
    use_retry_after: bool = False  # брать паузу из retry_after ответа (429 Telegram)


def default_policies() -> dict[str, RetryPolicy]:
    """Повторы: Telegram — по retry_after, MAX — при 429/5xx, WhatsApp — один повтор через 60 с."""
    return {
        NETWORK_TG: RetryPolicy(attempts=3, delay=5.0, use_retry_after=True),
        NETWORK_MAX: RetryPolicy(attempts=3, delay=5.0),
        NETWORK_WA: RetryPolicy(attempts=2, delay=60.0),
    }


# Текущий диапазон паузы между чатами (мин, макс), с — общий для всех сетей. Спрашивается
# перед каждой паузой, поэтому новый режим из меню действует и на уже идущую рассылку.
DelayProvider = Callable[[], Awaitable[tuple[float, float]]]
# Кнопки под сообщением-прогрессом идущей рассылки («⏹ Остановить»), по номеру рассылки.
ProgressKeyboard = Callable[[int], InlineKeyboardMarkup]


@dataclass(slots=True)
class WorkerConfig:
    delay_min: float = 10.0  # используются, если DelayProvider не передан
    delay_max: float = 30.0
    max_part_delay: float = 1.0  # между частями одного поста в MAX
    wa_check_interval: float = 30.0  # как часто проверять WA-сессию на паузе
    error_backoff: float = 10.0  # пауза после непредвиденной ошибки дорожки
    policies: dict[str, RetryPolicy] = field(default_factory=default_policies)


class Worker:
    def __init__(
        self,
        storage: Storage,
        bot: Bot,
        senders: Mapping[str, Sender],
        config: WorkerConfig | None = None,
        *,
        rng: random.Random | None = None,
        delay: DelayProvider | None = None,
        clock: Callable[[], float] | None = None,
        progress_kb: ProgressKeyboard | None = None,
    ) -> None:
        self._storage = storage
        self._bot = bot
        self._senders = senders
        self._config = config or WorkerConfig()
        self._rng = rng or random.Random()
        self._delay = delay
        self._clock = clock
        self._progress_kb = progress_kb
        self._wake = {net: asyncio.Event() for net in LANES}
        self._interrupt = {net: asyncio.Event() for net in LANES}
        self._active: dict[str, int | None] = dict.fromkeys(LANES)  # дорожка → рассылка
        self._last_send: dict[str, float] = {}  # дорожка → время последней отправки
        self._stopped: set[int] = set()  # рассылки, остановленные «Стопом»
        self._attempted: set[int] = set()  # рассылки, которые этот процесс уже брал в работу
        self._shutting_down = False
        self._tasks: list[asyncio.Task[None]] = []
        self._finish_lock = asyncio.Lock()  # завершение рассылки: отчёт ровно один раз
        self._progress_lock = asyncio.Lock()  # сообщение-прогресс создаётся один раз

    # --- управление ---

    @property
    def active_ids(self) -> set[int]:
        """Рассылки, которыми сейчас занята хотя бы одна дорожка."""
        return {bid for bid in self._active.values() if bid is not None}

    def wake(self) -> None:
        """Новая рассылка в очереди."""
        for event in self._wake.values():
            event.set()

    @property
    def stopped_ids(self) -> set[int]:
        """Рассылки, остановленные «Стопом» (в том числе ещё завершающиеся)."""
        return set(self._stopped)

    async def stop(self, ids: Collection[int] | None = None) -> list[int]:
        """Остановить выбранные незавершённые рассылки (None — все). Возвращает их номера.

        Остальные рассылки идут дальше: прерываются только дорожки, занятые остановленными.
        """
        ids = [
            b.id
            for b in await self._storage.unfinished()
            if b.id not in self._stopped and (ids is None or b.id in ids)
        ]
        if not ids:
            return []
        self._stopped.update(ids)
        for net, bid in self._active.items():
            if bid in self._stopped:
                self._interrupt[net].set()
        # Рассылки, которыми дорожки не заняты, завершаем сразу; остальные — дорожки сами.
        for bid in ids:
            await self._maybe_finish(bid)
        return ids

    def start(self) -> list[asyncio.Task[None]]:
        self._tasks = [
            asyncio.create_task(self._run_lane(net), name=f"broadcast-{net}") for net in LANES
        ]
        return self._tasks

    async def shutdown(self) -> None:
        """Остановка процесса: незаконченные рассылки продолжатся после старта."""
        self._shutting_down = True
        for net in LANES:
            self._interrupt[net].set()
            self._wake[net].set()
        for task in self._tasks:
            await task

    def _now(self) -> float:
        return self._clock() if self._clock is not None else asyncio.get_running_loop().time()

    async def _sleep(self, net: str, seconds: float) -> bool:
        """Прерываемая пауза дорожки. True — прервана («Стоп» или остановка процесса)."""
        event = self._interrupt[net]
        if event.is_set():
            return True
        if seconds <= 0:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    def _halted(self, bid: int) -> bool:
        return self._shutting_down or bid in self._stopped

    # --- дорожка ---

    async def _run_lane(self, net: str) -> None:
        log.info("Дорожка %s запущена", net)
        while not self._shutting_down:
            self._wake[net].clear()
            # Сбрасываем до выбора рассылки: «Стоп», пришедший после, не потеряется.
            self._interrupt[net].clear()
            try:
                broadcast = await self._next_for(net)
                if broadcast is None:
                    await self._finish_idle()
                    await self._wake[net].wait()
                    continue
                await self._lane_broadcast(net, broadcast)
            except Exception:
                log.exception("Ошибка дорожки %s", net)
                self._active[net] = None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake[net].wait(), self._config.error_backoff)
        log.info("Дорожка %s остановлена", net)

    async def _next_for(self, net: str) -> Broadcast | None:
        for broadcast in await self._storage.unfinished_with_pending(net):
            if broadcast.id not in self._stopped:
                return broadcast
        return None

    async def _finish_idle(self) -> None:
        """Рассылки, где отправлять больше нечего (например, всё ушло до перезапуска)."""
        for broadcast in await self._storage.unfinished():
            await self._maybe_finish(broadcast.id)

    async def _lane_broadcast(self, net: str, broadcast: Broadcast) -> None:
        bid = broadcast.id
        if self._halted(bid):
            return
        self._active[net] = bid
        try:
            await self._begin(broadcast)
            progress_id = await self._ensure_progress(broadcast)
            await self._send_lane(net, broadcast, progress_id)
        finally:
            self._active[net] = None
        await self._maybe_finish(bid)

    async def _begin(self, broadcast: Broadcast) -> None:
        bid = broadcast.id
        if bid not in self._attempted:
            self._attempted.add(bid)
            # «Продолжена после перезапуска» — только если её начал прошлый процесс.
            resumed = broadcast.status in ("running", "paused")
            log.info("Рассылка #%s: %s", bid, "возобновление" if resumed else "старт")
            if resumed:
                await self._notify(broadcast.admin_id, texts.RESUMED.format(id=bid))
        await self._storage.mark_started(bid)

    async def _send_lane(self, net: str, broadcast: Broadcast, progress_id: int | None) -> None:
        """Чаты сети по одному, со случайной паузой между отправками."""
        bid = broadcast.id
        source = SourceMessage(broadcast.source_chat_id, broadcast.source_message_id)
        parts = split_text(broadcast.text)
        prev_target: str | None = None
        for d in await self._storage.deliveries(bid):
            if d.network != net or d.status != "pending":
                continue
            if self._halted(bid):
                return
            if net == NETWORK_MAX and d.target_id == prev_target:
                pause = self._config.max_part_delay  # следующая часть того же поста
            else:
                pause = await self._pause_left(net)
            if await self._sleep(net, pause) or self._halted(bid):
                return
            if net == NETWORK_WA and not await self._wait_wa_ready(broadcast, progress_id):
                return
            if net == NETWORK_MAX:
                text = parts[d.part_no - 1] if 0 < d.part_no <= len(parts) else broadcast.text
                await self._deliver(d, text, None)
            elif net == NETWORK_TG:
                await self._deliver(d, broadcast.text, source)
            else:
                await self._deliver(d, broadcast.text, None)
            self._last_send[net] = self._now()
            prev_target = d.target_id
            await self._update_progress(broadcast, progress_id)

    async def _pause_left(self, net: str) -> float:
        """Сколько ещё ждать до следующей отправки в этой сети.

        Пауза отсчитывается от прошлой отправки дорожки, в том числе прошлой рассылки:
        после простоя первая отправка уходит сразу.
        """
        last = self._last_send.get(net)
        if last is None:
            return 0.0
        low, high = await self._delay_range()
        return max(0.0, self._rng.uniform(low, high) - (self._now() - last))

    async def _delay_range(self) -> tuple[float, float]:
        if self._delay is not None:
            return await self._delay()
        return self._config.delay_min, self._config.delay_max

    async def _wait_wa_ready(self, broadcast: Broadcast, progress_id: int | None) -> bool:
        """Если WA-сессия не open — ставим рассылку на паузу и ждём. False — прервано."""
        sender = self._senders.get(NETWORK_WA)
        if sender is None:
            return True  # доставка завершится ошибкой «не настроено»
        paused = False
        while True:
            health = await sender.health()
            if health.ok:
                if paused:
                    await self._storage.set_status(broadcast.id, "running")
                    await self._notify(broadcast.admin_id, texts.WA_RESUMED.format(id=broadcast.id))
                    await self._update_progress(broadcast, progress_id)
                return True
            if not paused:
                paused = True
                log.warning("Рассылка #%s: WA-сессия не open (%s)", broadcast.id, health.detail)
                await self._storage.set_status(broadcast.id, "paused")
                await self._notify(
                    broadcast.admin_id,
                    texts.WA_PAUSED.format(id=broadcast.id, detail=escape(health.detail)),
                )
                await self._update_progress(broadcast, progress_id, paused=True)
            if await self._sleep(NETWORK_WA, self._config.wa_check_interval) or self._halted(
                broadcast.id
            ):
                return False

    async def _deliver(self, d: Delivery, text: str, source: SourceMessage | None) -> None:
        sender = self._senders.get(d.network)
        if sender is None:
            await self._storage.finish_delivery(
                d.id, status="error", attempts=d.attempts, error=texts.NETWORK_NOT_CONFIGURED
            )
            return
        policy = self._config.policies[d.network]
        attempts = d.attempts
        for attempt in range(1, policy.attempts + 1):
            attempts += 1
            result = await sender.send_text(d.target_id, text, source=source)
            if result.ok:
                await self._storage.finish_delivery(
                    d.id, status="ok", attempts=attempts, message_id=result.message_id
                )
                return
            log.warning(
                "Доставка %s/%s (%s) попытка %s: %s",
                d.network,
                d.target_id,
                d.target_title,
                attempt,
                result.error,
            )
            if not result.retryable or attempt == policy.attempts:
                await self._storage.finish_delivery(
                    d.id, status="error", attempts=attempts, error=result.error
                )
                return
            await self._storage.set_attempts(d.id, attempts)
            delay = policy.delay
            if policy.use_retry_after and result.retry_after is not None:
                delay = result.retry_after
            if await self._sleep(d.network, delay):
                return  # останется pending: «Стоп» → skipped, рестарт → повторим

    # --- завершение ---

    async def _maybe_finish(self, bid: int) -> None:
        """Итоговый статус и отчёт — ровно один раз, когда дорожки рассылкой не заняты."""
        async with self._finish_lock:
            if bid in self.active_ids:
                return
            broadcast = await self._storage.get_broadcast(bid)
            if broadcast is None or broadcast.status in ("done", "stopped"):
                return
            has_pending = any(d.status == "pending" for d in await self._storage.deliveries(bid))
            stopped = has_pending and bid in self._stopped
            if has_pending and not stopped:
                return  # ещё в работе или ждёт перезапуска
            if stopped:
                await self._storage.skip_pending(bid, texts.STOPPED_BY_ADMIN)
            await self._storage.set_status(bid, "stopped" if stopped else "done")
            log.info("Рассылка #%s: %s", bid, "остановлена" if stopped else "готова")
            await self._update_progress(broadcast, broadcast.progress_message_id, final=True)
            final = await self._storage.deliveries(bid)
            if stopped and all(d.status == "skipped" for d in final):
                return  # остановлена до первой отправки — отчёт пустой, не шлём
            await self._notify(
                broadcast.admin_id,
                texts.report(
                    bid,
                    broadcast.networks,
                    [(d.network, d.target_title, d.status, d.error) for d in final],
                    stopped=stopped,
                ),
            )
            pruned = await self._storage.prune(HISTORY_KEEP_DAYS)
            if pruned:
                log.info("Удалено старых рассылок: %s", pruned)

    # --- сообщения админу ---

    async def _ensure_progress(self, broadcast: Broadcast) -> int | None:
        """Одно сообщение-прогресс на рассылку; создаёт та дорожка, что пришла первой."""
        async with self._progress_lock:
            current = await self._storage.get_broadcast(broadcast.id)
            if current is not None and current.progress_message_id is not None:
                return current.progress_message_id
            progress_id = await self._notify(
                broadcast.admin_id,
                await self._progress_text(broadcast),
                markup=self._markup(broadcast.id),
            )
            if progress_id is not None:
                await self._storage.set_progress_message(broadcast.id, progress_id)
            return progress_id

    async def _progress_text(self, broadcast: Broadcast, *, paused: bool = False) -> str:
        statuses: dict[str, list[str]] = {}
        for d in await self._storage.deliveries(broadcast.id):
            statuses.setdefault(d.network, []).append(d.status)
        return texts.progress(broadcast.id, broadcast.networks, statuses, paused=paused)

    def _markup(self, bid: int) -> InlineKeyboardMarkup | None:
        return self._progress_kb(bid) if self._progress_kb is not None else None

    async def _update_progress(
        self,
        broadcast: Broadcast,
        progress_id: int | None,
        *,
        paused: bool = False,
        final: bool = False,
    ) -> None:
        """final — рассылка завершена: кнопка «Остановить» убирается."""
        if progress_id is None:
            return
        text = await self._progress_text(broadcast, paused=paused)
        try:
            await self._bot.edit_message_text(
                text=text,
                chat_id=broadcast.admin_id,
                message_id=progress_id,
                reply_markup=None if final else self._markup(broadcast.id),
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in exc.message:
                log.warning("Не удалось обновить прогресс: %s", exc.message)
        except TelegramAPIError as exc:
            log.warning("Не удалось обновить прогресс: %s", exc.message)

    async def _notify(
        self, chat_id: int, text: str, *, markup: InlineKeyboardMarkup | None = None
    ) -> int | None:
        try:
            message = await self._bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        except TelegramAPIError as exc:
            log.warning("Не удалось написать админу: %s", exc.message)
            return None
        return message.message_id
