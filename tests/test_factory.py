from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx

from app.config import Settings
from app.senders.factory import build_senders
from app.senders.wa_evolution import EvolutionSender
from app.senders.wa_greenapi import GreenApiSender


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, tg_bot_token="1:x", **kw)  # type: ignore[call-arg]


async def test_provider_switch_without_code_changes() -> None:
    async with httpx.AsyncClient() as client:
        evo = build_senders(settings(evolution_api_key="k"), AsyncMock(), client)
        assert isinstance(evo.wa, EvolutionSender)
        green = build_senders(
            settings(wa_provider="greenapi", greenapi_id_instance="1", greenapi_api_token="t"),
            AsyncMock(),
            client,
        )
        assert isinstance(green.wa, GreenApiSender)
        assert set(green.by_network()) == {"tg", "wa"}
        empty = build_senders(settings(), AsyncMock(), client)
        assert empty.wa is None and empty.max is None
        assert set(empty.by_network()) == {"tg"}
        with_max = build_senders(settings(max_bot_token="m"), AsyncMock(), client)
        assert with_max.max is not None
