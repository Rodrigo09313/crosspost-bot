"""Хранилище рассылок и доставок в SQLite (WAL)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import aiosqlite

BroadcastStatus = Literal["queued", "running", "paused", "done", "stopped"]
DeliveryStatus = Literal["pending", "ok", "error", "skipped"]

UNFINISHED_STATUSES: tuple[BroadcastStatus, ...] = ("queued", "running", "paused")
HISTORY_KEEP_DAYS = 30  # завершённые рассылки старше этого срока бот удаляет сам

SCHEMA = """
CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    admin_id INTEGER NOT NULL,
    source_chat_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    networks TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id),
    network TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_title TEXT NOT NULL,
    part_no INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    message_id TEXT,
    error TEXT,
    sent_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_deliveries_broadcast ON deliveries(broadcast_id);
CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Broadcast:
    id: int
    created_at: datetime
    admin_id: int
    source_chat_id: int
    source_message_id: int
    text: str
    networks: list[str]
    status: BroadcastStatus
    progress_message_id: int | None


@dataclass(frozen=True, slots=True)
class Delivery:
    id: int
    broadcast_id: int
    network: str
    target_id: str
    target_title: str
    part_no: int
    status: DeliveryStatus
    attempts: int
    message_id: str | None
    error: str | None
    sent_at: str | None


@dataclass(frozen=True, slots=True)
class DeliveryPlan:
    network: str
    target_id: str
    target_title: str
    part_no: int = 1


def _broadcast(row: aiosqlite.Row) -> Broadcast:
    return Broadcast(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        admin_id=row["admin_id"],
        source_chat_id=row["source_chat_id"],
        source_message_id=row["source_message_id"],
        text=row["text"],
        networks=json.loads(row["networks"]),
        status=row["status"],
        progress_message_id=row["progress_message_id"],
    )


def _delivery(row: aiosqlite.Row) -> Delivery:
    return Delivery(
        id=row["id"],
        broadcast_id=row["broadcast_id"],
        network=row["network"],
        target_id=row["target_id"],
        target_title=row["target_title"],
        part_no=row["part_no"],
        status=row["status"],
        attempts=row["attempts"],
        message_id=row["message_id"],
        error=row["error"],
        sent_at=row["sent_at"],
    )


class Storage:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @classmethod
    async def open(cls, path: Path | str) -> Storage:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

    # --- рассылки ---

    async def create_broadcast(
        self,
        *,
        admin_id: int,
        source_chat_id: int,
        source_message_id: int,
        text: str,
        networks: Sequence[str],
        deliveries: Sequence[DeliveryPlan],
    ) -> int:
        """Создаёт рассылку и сразу все строки доставок в статусе pending (одна транзакция)."""
        cursor = await self._conn.execute(
            "INSERT INTO broadcasts (created_at, admin_id, source_chat_id, source_message_id,"
            " text, networks, status) VALUES (?, ?, ?, ?, ?, ?, 'queued')",
            (_now(), admin_id, source_chat_id, source_message_id, text, json.dumps(list(networks))),
        )
        broadcast_id = cursor.lastrowid
        assert broadcast_id is not None
        await self._conn.executemany(
            "INSERT INTO deliveries (broadcast_id, network, target_id, target_title, part_no)"
            " VALUES (?, ?, ?, ?, ?)",
            [(broadcast_id, d.network, d.target_id, d.target_title, d.part_no) for d in deliveries],
        )
        await self._conn.commit()
        return broadcast_id

    async def get_broadcast(self, broadcast_id: int) -> Broadcast | None:
        async with self._conn.execute(
            "SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)
        ) as cur:
            row = await cur.fetchone()
        return _broadcast(row) if row else None

    async def next_unfinished(self) -> Broadcast | None:
        """Самая старая незавершённая рассылка (queued/running/paused)."""
        async with self._conn.execute(
            "SELECT * FROM broadcasts WHERE status IN (?, ?, ?) ORDER BY id LIMIT 1",
            UNFINISHED_STATUSES,
        ) as cur:
            row = await cur.fetchone()
        return _broadcast(row) if row else None

    async def count_queued(self) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM broadcasts WHERE status = 'queued'"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_status(self, broadcast_id: int, status: BroadcastStatus) -> None:
        await self._conn.execute(
            "UPDATE broadcasts SET status = ? WHERE id = ?", (status, broadcast_id)
        )
        await self._conn.commit()

    async def mark_started(self, broadcast_id: int) -> None:
        """queued → running: первая из дорожек взяла рассылку в работу."""
        await self._conn.execute(
            "UPDATE broadcasts SET status = 'running' WHERE id = ? AND status = 'queued'",
            (broadcast_id,),
        )
        await self._conn.commit()

    async def unfinished_with_pending(self, network: str) -> list[Broadcast]:
        """Незавершённые рассылки, где в сети network ещё есть pending, старые первыми."""
        async with self._conn.execute(
            "SELECT * FROM broadcasts b WHERE b.status IN (?, ?, ?) AND EXISTS ("
            "SELECT 1 FROM deliveries d WHERE d.broadcast_id = b.id AND d.network = ?"
            " AND d.status = 'pending') ORDER BY b.id",
            (*UNFINISHED_STATUSES, network),
        ) as cur:
            rows = await cur.fetchall()
        return [_broadcast(r) for r in rows]

    async def set_progress_message(self, broadcast_id: int, message_id: int) -> None:
        await self._conn.execute(
            "UPDATE broadcasts SET progress_message_id = ? WHERE id = ?",
            (message_id, broadcast_id),
        )
        await self._conn.commit()

    async def same_text_recent(self, text: str, hours: int) -> list[Broadcast]:
        """Рассылки с таким же текстом: незавершённые и все за последние hours часов."""
        border = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        async with self._conn.execute(
            "SELECT * FROM broadcasts WHERE text = ? AND (status IN (?, ?, ?) OR created_at >= ?)"
            " ORDER BY id DESC",
            (text, *UNFINISHED_STATUSES, border),
        ) as cur:
            rows = await cur.fetchall()
        return [_broadcast(r) for r in rows]

    async def unfinished(self) -> list[Broadcast]:
        """Все незавершённые рассылки (queued/running/paused), старые первыми."""
        async with self._conn.execute(
            "SELECT * FROM broadcasts WHERE status IN (?, ?, ?) ORDER BY id", UNFINISHED_STATUSES
        ) as cur:
            rows = await cur.fetchall()
        return [_broadcast(r) for r in rows]

    async def network_counts(self, broadcast_id: int) -> dict[str, dict[str, int]]:
        """{сеть: {статус доставки: количество}}."""
        counts: dict[str, dict[str, int]] = {}
        async with self._conn.execute(
            "SELECT network, status, COUNT(*) AS n FROM deliveries WHERE broadcast_id = ?"
            " GROUP BY network, status",
            (broadcast_id,),
        ) as cur:
            for row in await cur.fetchall():
                counts.setdefault(row["network"], {})[row["status"]] = row["n"]
        return counts

    async def recent(self, limit: int = 5) -> list[tuple[Broadcast, dict[str, dict[str, int]]]]:
        """Последние рассылки и количество доставок по сетям и статусам."""
        async with self._conn.execute(
            "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        return [(_broadcast(r), await self.network_counts(r["id"])) for r in rows]

    async def prune(self, keep_days: int) -> int:
        """Удаляет завершённые рассылки старше keep_days дней. Возвращает их число."""
        border = (datetime.now(UTC) - timedelta(days=keep_days)).isoformat(timespec="seconds")
        where = "status IN ('done', 'stopped') AND created_at < ?"
        await self._conn.execute(
            "DELETE FROM deliveries WHERE broadcast_id IN"
            f" (SELECT id FROM broadcasts WHERE {where})",
            (border,),
        )
        cursor = await self._conn.execute(f"DELETE FROM broadcasts WHERE {where}", (border,))
        await self._conn.commit()
        return cursor.rowcount

    # --- доставки ---

    async def deliveries(self, broadcast_id: int) -> list[Delivery]:
        async with self._conn.execute(
            "SELECT * FROM deliveries WHERE broadcast_id = ? ORDER BY id", (broadcast_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [_delivery(r) for r in rows]

    async def finish_delivery(
        self,
        delivery_id: int,
        *,
        status: DeliveryStatus,
        attempts: int,
        message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Записывает итог отправки сразу после неё."""
        await self._conn.execute(
            "UPDATE deliveries SET status = ?, attempts = ?, message_id = ?, error = ?,"
            " sent_at = ? WHERE id = ?",
            (status, attempts, message_id, error, _now(), delivery_id),
        )
        await self._conn.commit()

    async def set_attempts(self, delivery_id: int, attempts: int) -> None:
        await self._conn.execute(
            "UPDATE deliveries SET attempts = ? WHERE id = ?", (attempts, delivery_id)
        )
        await self._conn.commit()

    async def skip_pending(self, broadcast_id: int, reason: str) -> int:
        cursor = await self._conn.execute(
            "UPDATE deliveries SET status = 'skipped', error = ? "
            "WHERE broadcast_id = ? AND status = 'pending'",
            (reason, broadcast_id),
        )
        await self._conn.commit()
        return cursor.rowcount

    # --- настройки из меню бота ---

    async def get_setting(self, key: str) -> str | None:
        async with self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return str(row["value"]) if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self._conn.commit()
