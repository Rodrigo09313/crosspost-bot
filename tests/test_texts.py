from __future__ import annotations

from datetime import datetime, timedelta

from app import texts
from app.storage import DeliveryPlan, Storage


def test_when_today_yesterday_date() -> None:
    now = datetime(2026, 10, 30, 20, 0).astimezone()
    assert texts.when(now.replace(hour=9, minute=5), now) == "сегодня 09:05"
    assert texts.when(now - timedelta(days=1), now) == "вчера 20:00"
    assert texts.when(now - timedelta(days=3), now) == "27.10 20:00"


def test_post_title_first_line_trimmed() -> None:
    # rich-таблица — не HTML, поэтому без экранирования
    assert texts.post_title("\n\n  Аванс <50%>  \nвторая строка") == "Аванс <50%>"
    assert texts.post_title("А" * 100) == "А" * 24 + "…"


def test_progress_cell() -> None:
    assert texts.progress_cell(None) == "—"
    assert texts.progress_cell({"ok": 45, "pending": 150}) == "45/195 ⏳"
    assert texts.progress_cell({"pending": 3}, paused=True) == "⏸ пауза"
    assert texts.progress_cell({"ok": 4}) == "4/4 ✅"
    assert texts.progress_cell({"ok": 190, "error": 5}) == "190/195 ❌"
    assert texts.progress_cell({"ok": 2, "skipped": 1}) == "2/3 ⏹"


async def test_history_shows_last_five(storage: Storage) -> None:
    for _ in range(7):
        await storage.create_broadcast(
            admin_id=1,
            source_chat_id=1,
            source_message_id=1,
            text="t",
            networks=["tg"],
            deliveries=[DeliveryPlan("tg", "-100", "Telegram")],
        )
    assert [b.id for b, _ in await storage.recent()] == [7, 6, 5, 4, 3]
