from __future__ import annotations

from app.storage import DeliveryPlan, Storage


async def _create(storage: Storage) -> int:
    return await storage.create_broadcast(
        admin_id=1,
        source_chat_id=1,
        source_message_id=10,
        text="Привет 🔴",
        networks=["tg", "wa"],
        deliveries=[
            DeliveryPlan("tg", "-100", "Telegram"),
            DeliveryPlan("wa", "a@g.us", "Группа A"),
            DeliveryPlan("wa", "b@g.us", "Группа B"),
        ],
    )


async def test_create_broadcast_with_pending_deliveries(storage: Storage) -> None:
    bid = await _create(storage)
    b = await storage.get_broadcast(bid)
    assert b is not None
    assert b.status == "queued"
    assert b.networks == ["tg", "wa"]
    assert b.text == "Привет 🔴"
    deliveries = await storage.deliveries(bid)
    assert [d.status for d in deliveries] == ["pending"] * 3
    assert [d.target_title for d in deliveries] == ["Telegram", "Группа A", "Группа B"]


async def test_finish_and_skip(storage: Storage) -> None:
    bid = await _create(storage)
    d1, d2, _ = await storage.deliveries(bid)
    await storage.finish_delivery(d1.id, status="ok", attempts=1, message_id="55")
    await storage.finish_delivery(d2.id, status="error", attempts=2, error="boom")
    assert await storage.skip_pending(bid, "stopped") == 1
    statuses = {
        d.target_id: (d.status, d.message_id, d.error) for d in await storage.deliveries(bid)
    }
    assert statuses == {
        "-100": ("ok", "55", None),
        "a@g.us": ("error", None, "boom"),
        "b@g.us": ("skipped", None, "stopped"),
    }


async def test_next_unfinished_and_recent(storage: Storage) -> None:
    first = await _create(storage)
    second = await _create(storage)
    nxt = await storage.next_unfinished()
    assert nxt is not None and nxt.id == first
    assert await storage.count_queued() == 2
    await storage.set_status(first, "done")
    nxt = await storage.next_unfinished()
    assert nxt is not None and nxt.id == second
    await storage.set_status(second, "paused")
    nxt = await storage.next_unfinished()
    assert nxt is not None and nxt.status == "paused"
    await storage.set_progress_message(second, 777)
    recent = await storage.recent(10)
    assert [b.id for b, _ in recent] == [second, first]
    assert recent[0][0].progress_message_id == 777
    assert recent[0][1] == {"tg": {"pending": 1}, "wa": {"pending": 2}}
    assert [b.id for b in await storage.unfinished()] == [second]


async def test_prune_keeps_recent_and_unfinished(storage: Storage) -> None:
    old_done = await _create(storage)
    old_running = await _create(storage)
    fresh = await _create(storage)
    await storage.set_status(old_done, "done")
    await storage.set_status(old_running, "running")
    await storage.set_status(fresh, "done")
    await storage._conn.execute(
        "UPDATE broadcasts SET created_at = '2020-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
        (old_done, old_running),
    )
    await storage._conn.commit()
    assert await storage.prune(30) == 1
    assert await storage.get_broadcast(old_done) is None
    assert await storage.deliveries(old_done) == []
    assert await storage.get_broadcast(old_running) is not None  # незавершённые не трогаем
    assert await storage.get_broadcast(fresh) is not None


async def test_data_survives_reopen(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "db.sqlite3"
    st = await Storage.open(path)
    bid = await _create(st)
    await st.set_status(bid, "running")
    await st.close()
    st2 = await Storage.open(path)
    b = await st2.next_unfinished()
    await st2.close()
    assert b is not None and b.id == bid and b.status == "running"
