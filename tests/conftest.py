from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from app.storage import Storage


@pytest.fixture
async def storage(tmp_path: Path) -> AsyncIterator[Storage]:
    st = await Storage.open(tmp_path / "bot.sqlite3")
    try:
        yield st
    finally:
        await st.close()


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client
