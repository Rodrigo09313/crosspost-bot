from __future__ import annotations

import pytest

from app.config import Settings


def make(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_csv_lists_and_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(
        monkeypatch,
        TG_BOT_TOKEN="1:abc",
        ADMIN_IDS="1, 2,3",
        WA_GROUP_IDS="a@g.us, b@g.us,",
        TG_TARGET_CHAT_ID="",
        MAX_TARGET_CHAT_ID="",
    )
    assert s.admin_ids == [1, 2, 3]
    assert s.wa_group_ids == ["a@g.us", "b@g.us"]
    assert s.tg_target_chat_id is None
    assert s.max_target_chat_id is None
    assert not s.max_enabled
    assert s.wa_provider == "evolution"


def test_secrets_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    s = make(monkeypatch, TG_BOT_TOKEN="1:abc", MAX_BOT_TOKEN="maxtok", EVOLUTION_API_KEY="evo")
    assert set(s.secrets()) == {"1:abc", "maxtok", "evo"}
