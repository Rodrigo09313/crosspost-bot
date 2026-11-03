from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app import texts
from app.senders.base import HealthStatus, SendResult, SourceMessage
from app.storage import DeliveryPlan, Storage
from app.worker import RetryPolicy, Worker, WorkerConfig
from tests.helpers import ADMIN_ID, MockedBot

WA_GROUPS = ["a@g.us", "b@g.us", "c@g.us"]


@dataclass
class FakeSender:
    network: str
    sent: list[tuple[str, str, SourceMessage | None]] = field(default_factory=list)
    scripted: dict[str, deque[SendResult]] = field(default_factory=lambda: defaultdict(deque))
    healthy: bool = True
    on_send: asyncio.Event | None = None

    async def health(self) -> HealthStatus:
        return HealthStatus(self.healthy, "open" if self.healthy else "close")

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult:
        self.sent.append((chat_id, text, source))
        if self.on_send is not None:
            self.on_send.set()
        queue = self.scripted.get(chat_id)
        if queue:
            return queue.popleft()
        return SendResult.success(f"{self.network}-{len(self.sent)}")

    def targets(self) -> list[str]:
        return [t for t, _, _ in self.sent]


def fast_config(delay: float = 0.0, **kw: float) -> WorkerConfig:
    cfg = WorkerConfig(
        delay_min=delay,
        delay_max=delay,
        max_part_delay=kw.pop("max_part_delay", 0.0),
        wa_check_interval=kw.pop("wa_check_interval", 0.01),
        policies={
            "tg": RetryPolicy(3, 0.0, use_retry_after=True),
            "max": RetryPolicy(3, 0.0),
            "wa": RetryPolicy(2, 0.0),
        },
    )
    assert not kw
    return cfg


@dataclass
class Env:
    storage: Storage
    bot: MockedBot
    tg: FakeSender
    max: FakeSender
    wa: FakeSender

    def worker(self, config: WorkerConfig | None = None, **kw: object) -> Worker:
        return Worker(
            self.storage,
            self.bot,
            {"tg": self.tg, "max": self.max, "wa": self.wa},
            config or fast_config(),
            rng=random.Random(0),
            **kw,  # type: ignore[arg-type]
        )


@pytest.fixture
async def env(storage: Storage) -> Env:
    return Env(storage, MockedBot(), FakeSender("tg"), FakeSender("max"), FakeSender("wa"))


async def create(
    storage: Storage,
    text: str = "Пост 🔴",
    *,
    tg: list[str] | None = None,
    max_chats: list[str] | None = None,
    max_parts: int = 1,
    wa: list[str] | None = None,
) -> int:
    tg = ["-100"] if tg is None else tg
    max_chats = ["777"] if max_chats is None else max_chats
    wa = WA_GROUPS if wa is None else wa
    plans = [DeliveryPlan("tg", c, f"TG {c}") for c in tg]
    plans += [
        DeliveryPlan("max", c, f"MAX {c}", n) for c in max_chats for n in range(1, max_parts + 1)
    ]
    plans += [DeliveryPlan("wa", g, f"Группа {g[0].upper()}") for g in wa]
    networks = [n for n, items in (("tg", tg), ("max", max_chats), ("wa", wa)) if items]
    return await storage.create_broadcast(
        admin_id=ADMIN_ID,
        source_chat_id=ADMIN_ID,
        source_message_id=10,
        text=text,
        networks=networks,
        deliveries=plans,
    )


async def run_until_idle(worker: Worker, storage: Storage, timeout: float = 5) -> None:
    tasks = worker.start()
    deadline = time.monotonic() + timeout
    while await storage.next_unfinished() is not None:
        assert time.monotonic() < deadline, "рассылка не завершилась"
        await asyncio.sleep(0.01)
    await worker.shutdown()
    assert all(t.done() for t in tasks)


async def statuses(storage: Storage, bid: int) -> dict[str, str]:
    return {
        f"{d.network}:{d.target_id}:{d.part_no}": d.status for d in await storage.deliveries(bid)
    }


async def wait_for(predicate: Callable[[], Awaitable[bool]], timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while not await predicate():
        assert time.monotonic() < deadline, "не дождались"
        await asyncio.sleep(0.01)


def status_is(storage: Storage, bid: int, status: str) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        b = await storage.get_broadcast(bid)
        return b is not None and b.status == status

    return check


# --- полный проход ---


async def test_full_broadcast(env: Env) -> None:
    text = "\n\n".join(["А" * 2500, "Б" * 2500])
    bid = await create(env.storage, text, max_parts=2)
    await run_until_idle(env.worker(), env.storage)

    b = await env.storage.get_broadcast(bid)
    assert b is not None and b.status == "done"
    assert set((await statuses(env.storage, bid)).values()) == {"ok"}
    # Telegram — copy_message из лички админа
    assert env.tg.sent == [("-100", text, SourceMessage(ADMIN_ID, 10))]
    # MAX — по частям, по порядку
    assert [t for _, t, _ in env.max.sent] == ["А" * 2500, "Б" * 2500]
    assert env.wa.targets() == WA_GROUPS
    assert b.progress_message_id is not None
    assert len(env.bot.sent("SendMessage")) == 2  # один прогресс на все дорожки + отчёт
    report = env.bot.texts()[-1]
    assert f"Рассылка #{bid} завершена" in report
    assert "✅ WhatsApp 3/3 групп" in report and "✅ MAX 2/2 част." in report
    progress = [c.text for c in env.bot.sent("EditMessageText")]
    assert any("WA 1/3" in p for p in progress)
    assert any("TG 1/1 ✅ · MAX 2/2 ✅ · WA 3/3 ✅" in p for p in progress)


# --- паузы: одна настройка на все сети ---


async def test_pause_between_chats_in_every_network(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = env.worker(fast_config(20, max_part_delay=1.0), clock=lambda: 0.0)
    slept: dict[str, list[float]] = defaultdict(list)

    async def fake_sleep(net: str, seconds: float) -> bool:
        slept[net].append(seconds)
        return False

    monkeypatch.setattr(worker, "_sleep", fake_sleep)
    await create(env.storage, tg=["-1", "-2"], max_chats=["m1", "m2"], max_parts=2)
    await run_until_idle(worker, env.storage)
    # перед первым чатом сети пауза 0, дальше — между чатами; части одного поста MAX — 1 с
    assert slept["tg"] == [0.0, 20]
    assert slept["max"] == [0.0, 1.0, 20, 1.0]
    assert slept["wa"] == [0.0, 20, 20]


async def test_pause_counts_from_last_send(env: Env) -> None:
    """Пауза отсчитывается от прошлой отправки сети: прошедшее время вычитается."""
    now = [0.0]
    worker = env.worker(fast_config(30), clock=lambda: now[0])
    assert await worker._pause_left("wa") == 0.0  # сеть ещё не отправляла — сразу
    worker._last_send["wa"] = 0.0
    now[0] = 10.0
    assert await worker._pause_left("wa") == 20.0
    now[0] = 45.0
    assert await worker._pause_left("wa") == 0.0  # после простоя — сразу
    assert await worker._pause_left("tg") == 0.0  # у каждой сети свой отсчёт


async def test_delay_from_provider(env: Env) -> None:
    """Пауза берётся из настроек меню перед каждой паузой, а не из WorkerConfig."""
    calls: list[int] = []

    async def provider() -> tuple[float, float]:
        calls.append(1)
        return 0.0, 0.0

    worker = env.worker(fast_config(30), delay=provider)  # без провайдера ждали бы по 30 с
    bid = await create(env.storage, tg=["-1", "-2"])
    await run_until_idle(worker, env.storage)
    assert env.wa.targets() == WA_GROUPS
    assert len(calls) == 1 + 2  # TG: 2 чата → 1 пауза; MAX 1 чат → 0; WA: 3 группы → 2
    assert await status_is(env.storage, bid, "done")()


# --- параллельные дорожки ---


async def test_networks_run_in_parallel(env: Env) -> None:
    """Каждая сеть начинает сразу и не ждёт пауз других сетей."""
    env.tg.on_send, env.max.on_send, env.wa.on_send = (asyncio.Event() for _ in range(3))
    worker = env.worker(fast_config(30))
    bid = await create(env.storage, tg=["-1", "-2"], max_chats=["m1", "m2"])
    worker.start()
    for sender in (env.tg, env.max, env.wa):
        assert sender.on_send is not None
        await asyncio.wait_for(sender.on_send.wait(), 3)
    await asyncio.sleep(0.05)
    # во всех трёх сетях ушёл первый чат, дальше у каждой своя пауза 30 с
    assert env.tg.targets() == ["-1"] and env.max.targets() == ["m1"]
    assert env.wa.targets() == ["a@g.us"]
    assert await status_is(env.storage, bid, "running")()
    await worker.shutdown()


async def test_tg_of_next_broadcast_not_waiting_wa(env: Env) -> None:
    """Пока WhatsApp рассылки #1 на паузе, Telegram рассылки #2 уходит и она завершается."""
    env.wa.on_send = asyncio.Event()
    now = [0.0]
    worker = env.worker(fast_config(30), clock=lambda: now[0])
    first = await create(env.storage, max_chats=[])
    worker.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)  # #1: первая группа, дальше пауза 30 с
    await asyncio.sleep(0.05)  # бот записал время отправки
    now[0] += 100  # пауза Telegram с прошлой отправки давно прошла

    second = await create(env.storage, "Второй пост", max_chats=[], wa=[])
    worker.wake()
    await wait_for(status_is(env.storage, second, "done"))

    async def reported() -> bool:
        return any(f"Рассылка #{second} завершена" in t for t in env.bot.texts())

    await wait_for(reported)
    assert env.tg.targets() == ["-100", "-100"]
    assert env.wa.targets() == ["a@g.us"]  # WhatsApp #1 по-прежнему на паузе
    assert await status_is(env.storage, first, "running")()
    await worker.shutdown()


async def test_lane_keeps_broadcast_order(env: Env) -> None:
    first = await create(env.storage, "Первый")
    second = await create(env.storage, "Второй")
    await run_until_idle(env.worker(), env.storage)
    assert [t for _, t, _ in env.wa.sent] == ["Первый"] * 3 + ["Второй"] * 3
    assert [t for _, t, _ in env.tg.sent] == ["Первый", "Второй"]
    for bid in (first, second):
        assert await status_is(env.storage, bid, "done")()


async def test_each_delivery_sent_once_and_one_report(env: Env) -> None:
    bids = [await create(env.storage, f"Пост {i}") for i in range(3)]
    bids.append(await create(env.storage, "Только TG", max_chats=[], wa=[]))
    await run_until_idle(env.worker(), env.storage)
    assert len(env.tg.sent) == 4
    assert len(env.max.sent) == 3
    assert env.wa.targets() == WA_GROUPS * 3
    for bid in bids:
        reports = [t for t in env.bot.texts() if f"Рассылка #{bid} завершена" in t]
        assert len(reports) == 1


async def test_wake_picks_new_broadcast(env: Env) -> None:
    worker = env.worker()
    worker.start()
    await asyncio.sleep(0.05)
    bid = await create(env.storage)
    worker.wake()
    await wait_for(status_is(env.storage, bid, "done"))
    await worker.shutdown()


# --- повторы ---


async def test_retries_and_errors(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.tg.scripted["-100"].append(SendResult.failure("Flood", retryable=True, retry_after=7))
    env.max.scripted["777"].extend([SendResult.failure("HTTP 502", retryable=True)] * 3)
    env.wa.scripted["a@g.us"].append(SendResult.failure("HTTP 500", retryable=True))
    env.wa.scripted["b@g.us"].append(SendResult.failure("HTTP 400: not a group"))
    worker = env.worker(
        WorkerConfig(
            delay_min=0,
            delay_max=0,
            max_part_delay=0,
            policies={
                "tg": RetryPolicy(3, 5.0, use_retry_after=True),
                "max": RetryPolicy(3, 5.0),
                "wa": RetryPolicy(2, 60.0),
            },
        )
    )
    slept: dict[str, list[float]] = defaultdict(list)

    async def fake_sleep(net: str, seconds: float) -> bool:
        if seconds:
            slept[net].append(seconds)
        return False

    monkeypatch.setattr(worker, "_sleep", fake_sleep)
    bid = await create(env.storage)
    await run_until_idle(worker, env.storage)

    by_target = {d.target_id: d for d in await env.storage.deliveries(bid)}
    assert (by_target["-100"].status, by_target["-100"].attempts) == ("ok", 2)
    assert (by_target["777"].status, by_target["777"].attempts) == ("error", 3)
    assert (by_target["a@g.us"].status, by_target["a@g.us"].attempts) == ("ok", 2)
    assert (by_target["b@g.us"].status, by_target["b@g.us"].attempts) == ("error", 1)
    assert by_target["b@g.us"].error == "HTTP 400: not a group"
    assert by_target["c@g.us"].status == "ok"
    # TG ждал retry_after=7, MAX — 5 с дважды, WA — 60 с перед повтором
    assert slept == {"tg": [7], "max": [5.0, 5.0], "wa": [60.0]}
    report = env.bot.texts()[-1]
    assert "❌ MAX 0/1 част." in report
    assert "❌ Группа B — HTTP 400: not a group" in report


# --- «Стоп» ---


async def test_interruptible_sleep(env: Env) -> None:
    """«Стоп» прерывает паузы только дорожек, занятых остановленной рассылкой."""
    worker = env.worker()
    first = await create(env.storage)
    second = await create(env.storage)
    worker._active.update({"tg": first, "max": first, "wa": second})
    started = time.monotonic()
    sleepers = {net: asyncio.create_task(worker._sleep(net, 30)) for net in ("tg", "max", "wa")}
    await asyncio.sleep(0.05)
    assert await worker.stop([first]) == [first]
    assert await sleepers["tg"] is True and await sleepers["max"] is True
    assert time.monotonic() - started < 1
    await asyncio.sleep(0.05)
    assert not sleepers["wa"].done()  # WhatsApp рассылки #2 пауза не прервана
    sleepers["wa"].cancel()


async def test_stop_one_keeps_others(env: Env) -> None:
    """Остановка одной рассылки: следующая в очереди идёт дальше."""
    env.wa.on_send = asyncio.Event()
    now = [0.0]
    worker = env.worker(fast_config(30), clock=lambda: now[0])
    first = await create(env.storage, "Первый", tg=[], max_chats=[])
    second = await create(env.storage, "Второй", tg=[], max_chats=[], wa=["z@g.us"])
    worker.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)  # #1: первая группа, дальше пауза 30 с
    await asyncio.sleep(0.05)  # бот записал время отправки
    now[0] += 100  # пауза WhatsApp к #2 уже прошла
    assert await worker.stop([first]) == [first]
    assert worker.stopped_ids == {first}
    await wait_for(status_is(env.storage, first, "stopped"))
    await wait_for(status_is(env.storage, second, "done"))
    assert env.wa.sent == [("a@g.us", "Первый", None), ("z@g.us", "Второй", None)]
    await worker.shutdown()


async def test_progress_has_stop_button_until_finished(env: Env) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    def kb(bid: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"stop {bid}", callback_data="x")]]
        )

    worker = env.worker(progress_kb=kb)
    bid = await create(env.storage)
    await run_until_idle(worker, env.storage)
    (progress,) = [c for c in env.bot.sent("SendMessage") if c.text.startswith("📤")]
    assert progress.reply_markup == kb(bid)
    edits = env.bot.sent("EditMessageText")
    assert edits[0].reply_markup == kb(bid)
    assert edits[-1].reply_markup is None  # завершена — кнопки нет


async def test_stop_before_start_sends_no_report(env: Env) -> None:
    bid = await create(env.storage)
    worker = env.worker()
    assert await worker.stop() == [bid]
    assert await status_is(env.storage, bid, "stopped")()
    assert env.bot.texts() == []  # ничего не отправлено — пустой отчёт не шлём


async def test_stop_during_pauses(env: Env) -> None:
    env.wa.on_send, env.tg.on_send = asyncio.Event(), asyncio.Event()
    worker = env.worker(fast_config(30))
    bid = await create(env.storage, tg=["-1", "-2"])
    worker.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)  # первые чаты ушли, дальше паузы 30 с
    await asyncio.wait_for(env.tg.on_send.wait(), 3)
    await asyncio.sleep(0.05)
    started = time.monotonic()
    assert await worker.stop() == [bid]
    await wait_for(status_is(env.storage, bid, "stopped"))
    assert time.monotonic() - started < 1.5
    assert env.wa.targets() == ["a@g.us"] and env.tg.targets() == ["-1"]
    st = await statuses(env.storage, bid)
    assert st["wa:b@g.us:1"] == st["wa:c@g.us:1"] == st["tg:-2:1"] == "skipped"
    assert st["wa:a@g.us:1"] == st["tg:-1:1"] == st["max:777:1"] == "ok"

    async def reported() -> bool:
        return any(f"Рассылка #{bid} остановлена" in t for t in env.bot.texts())

    await wait_for(reported)
    assert len([t for t in env.bot.texts() if f"Рассылка #{bid} остановлена" in t]) == 1
    assert worker.active_ids == set()
    await worker.shutdown()


async def test_stop_without_broadcast(env: Env) -> None:
    assert await env.worker().stop() == []


async def test_stop_not_started_broadcast_then_idle(env: Env) -> None:
    """«Стоп» до старта дорожек: рассылка сразу остановлена, ничего не отправлено."""
    bid = await create(env.storage)
    worker = env.worker()
    assert await worker.stop() == [bid]
    assert await status_is(env.storage, bid, "stopped")()
    await run_until_idle(worker, env.storage)
    assert env.tg.sent == env.max.sent == env.wa.sent == []


async def test_stop_does_not_block_next_broadcast(env: Env) -> None:
    env.wa.on_send = asyncio.Event()
    worker = env.worker(fast_config(30))
    first = await create(env.storage, tg=[], max_chats=[])
    worker.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)
    assert await worker.stop() == [first]
    await wait_for(status_is(env.storage, first, "stopped"))

    second = await create(env.storage, "Второй", max_chats=[], wa=[])
    worker.wake()
    await wait_for(status_is(env.storage, second, "done"))
    assert (await statuses(env.storage, second))["tg:-100:1"] == "ok"
    await worker.shutdown()


async def test_stop_while_wa_paused(env: Env) -> None:
    env.wa.healthy = False
    bid = await create(env.storage)
    worker = env.worker(fast_config(wa_check_interval=60))
    worker.start()
    await wait_for(status_is(env.storage, bid, "paused"))
    await worker.stop()
    started = time.monotonic()
    await wait_for(status_is(env.storage, bid, "stopped"))
    assert time.monotonic() - started < 1
    await worker.shutdown()


# --- возобновление после рестарта ---


async def test_resume_after_restart_without_duplicates(env: Env, tmp_path: Path) -> None:
    """Процесс остановлен посреди WA-рассылки → новый процесс продолжает с pending."""
    env.wa.on_send = asyncio.Event()
    worker1 = env.worker(fast_config(30))
    bid = await create(env.storage)
    worker1.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)
    await asyncio.sleep(0.05)
    await worker1.shutdown()  # SIGTERM / docker compose restart

    assert await status_is(env.storage, bid, "running")()
    st = await statuses(env.storage, bid)
    assert st["wa:a@g.us:1"] == "ok" and st["wa:b@g.us:1"] == "pending"

    # «Новый процесс»: новое соединение с той же БД, новые отправщики и воркер
    path = tmp_path / "bot.sqlite3"
    storage2 = await Storage.open(path)
    try:
        env2 = Env(storage2, MockedBot(), FakeSender("tg"), FakeSender("max"), FakeSender("wa"))
        await run_until_idle(env2.worker(), storage2)
        assert env2.tg.sent == [] and env2.max.sent == []
        assert env2.wa.targets() == ["b@g.us", "c@g.us"]
        all_wa = env.wa.targets() + env2.wa.targets()
        assert sorted(all_wa) == WA_GROUPS
        assert len(env.tg.sent) == 1 and len(env.max.sent) == 1
        assert await status_is(storage2, bid, "done")()
        assert env2.bot.texts().count(texts.RESUMED.format(id=bid)) == 1
        assert set((await statuses(storage2, bid)).values()) == {"ok"}
    finally:
        await storage2.close()


async def test_resume_paused_broadcast_from_db(env: Env) -> None:
    bid = await create(env.storage)
    deliveries = await env.storage.deliveries(bid)
    for d in deliveries[:3]:  # tg, max, wa a — уже отправлены до «падения»
        await env.storage.finish_delivery(d.id, status="ok", attempts=1)
    await env.storage.set_status(bid, "paused")
    await run_until_idle(env.worker(), env.storage)
    assert env.tg.sent == [] and env.max.sent == []
    assert env.wa.targets() == ["b@g.us", "c@g.us"]
    assert texts.RESUMED.format(id=bid) in env.bot.texts()


async def test_finishes_broadcast_with_nothing_left(env: Env) -> None:
    """Всё ушло, но процесс упал до отчёта: после старта рассылка завершается с отчётом."""
    bid = await create(env.storage)
    for d in await env.storage.deliveries(bid):
        await env.storage.finish_delivery(d.id, status="ok", attempts=1)
    await env.storage.set_status(bid, "running")
    await run_until_idle(env.worker(), env.storage)
    assert await status_is(env.storage, bid, "done")()
    assert f"Рассылка #{bid} завершена" in env.bot.texts()[-1]


async def test_shutdown_keeps_broadcast_running(env: Env) -> None:
    env.wa.on_send = asyncio.Event()
    worker = env.worker(fast_config(30))
    bid = await create(env.storage)
    worker.start()
    await asyncio.wait_for(env.wa.on_send.wait(), 3)
    await worker.shutdown()
    assert await status_is(env.storage, bid, "running")()
    assert not [t for t in env.bot.texts() if "остановлена" in t or "завершена" in t]


# --- WA-сессия не подключена ---


async def test_wa_session_down_pauses_and_resumes(env: Env) -> None:
    env.wa.healthy = False
    bid = await create(env.storage)
    worker = env.worker()
    worker.start()
    await wait_for(status_is(env.storage, bid, "paused"))
    assert env.wa.sent == []
    paused_msg = texts.WA_PAUSED.format(id=bid, detail="close")
    assert paused_msg in env.bot.texts()
    assert "/wa_qr" in paused_msg

    async def direct_sent() -> bool:
        return len(env.tg.sent) == 1 and len(env.max.sent) == 1

    await wait_for(direct_sent)  # TG и MAX не ждут WhatsApp
    await asyncio.sleep(0.05)
    assert env.bot.texts().count(paused_msg) == 1  # админу пишем один раз
    env.wa.healthy = True
    await wait_for(status_is(env.storage, bid, "done"))
    assert env.wa.targets() == WA_GROUPS
    assert texts.WA_RESUMED.format(id=bid) in env.bot.texts()
    await worker.shutdown()


# --- прогресс ---


async def test_progress_not_modified_is_ignored(env: Env) -> None:
    env.bot.edit_error = "Bad Request: message is not modified"
    bid = await create(env.storage)
    await run_until_idle(env.worker(), env.storage)
    assert await status_is(env.storage, bid, "done")()
    assert env.bot.sent("EditMessageText")  # ошибка была, рассылка не упала
    assert f"Рассылка #{bid} завершена" in env.bot.texts()[-1]
