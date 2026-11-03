from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app import texts
from app.senders.base import SenderError
from app.senders.max import RUSSIAN_ROOT_CA, MaxSender, make_max_http_client

BASE = "https://platform-api2.max.ru"
TOKEN = "max-secret-token"
# Russian Trusted Root CA (gu-st.ru), действует до 27.02.2032
ROOT_CA_SHA256 = "D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31"


@pytest.fixture
async def sender() -> AsyncIterator[MaxSender]:
    async with httpx.AsyncClient() as client:
        yield MaxSender(client, BASE + "/", TOKEN)


@respx.mock
async def test_send_text_ok(sender: MaxSender) -> None:
    route = respx.post(f"{BASE}/messages").mock(
        return_value=httpx.Response(200, json={"message": {"body": {"mid": "mid.123"}}})
    )
    result = await sender.send_text("777", "Привет 🔴")
    assert result.ok and result.message_id == "mid.123"
    request = route.calls.last.request
    assert request.url.params["chat_id"] == "777"
    assert request.headers["Authorization"] == TOKEN
    assert json.loads(request.read()) == {"text": "Привет 🔴"}


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (500, True), (503, True), (400, False), (401, False), (403, False)],
)
@respx.mock
async def test_send_text_errors(sender: MaxSender, status: int, retryable: bool) -> None:
    respx.post(f"{BASE}/messages").mock(
        return_value=httpx.Response(status, json={"code": "x", "message": "описание"})
    )
    result = await sender.send_text("777", "t")
    assert not result.ok
    assert result.retryable is retryable
    assert result.error == f"HTTP {status}: описание"


@respx.mock
async def test_send_text_network_error_is_retryable(sender: MaxSender) -> None:
    respx.post(f"{BASE}/messages").mock(side_effect=httpx.ConnectTimeout("timeout"))
    result = await sender.send_text("777", "t")
    assert not result.ok and result.retryable


@respx.mock
async def test_send_text_read_timeout_not_retried(sender: MaxSender) -> None:
    respx.post(f"{BASE}/messages").mock(side_effect=httpx.ReadTimeout("timeout"))
    result = await sender.send_text("777", "t")
    assert not result.ok and not result.retryable
    assert result.error is not None and texts.MAYBE_DELIVERED in result.error


def test_russian_root_ca_bundled() -> None:
    """Корень Минцифры для platform-api2.max.ru: тот самый файл и клиент с ним собирается."""
    pem = RUSSIAN_ROOT_CA.read_text()
    der = ssl.PEM_cert_to_DER_cert(pem)
    assert hashlib.sha256(der).hexdigest().upper() == ROOT_CA_SHA256
    client = make_max_http_client()
    assert isinstance(client, httpx.AsyncClient)


@respx.mock
async def test_health(sender: MaxSender) -> None:
    respx.get(f"{BASE}/me").mock(return_value=httpx.Response(200, json={"username": "my_bot"}))
    health = await sender.health()
    assert health.ok and health.detail == "my_bot"


@respx.mock
async def test_health_unauthorized(sender: MaxSender) -> None:
    respx.get(f"{BASE}/me").mock(
        return_value=httpx.Response(401, json={"code": "verify.token", "message": "Invalid"})
    )
    health = await sender.health()
    assert not health.ok and "401" in health.detail


@respx.mock
async def test_poll_chats_from_updates(sender: MaxSender) -> None:
    updates = {
        "marker": 5,
        "updates": [
            {"update_type": "bot_added", "chat_id": 100, "is_channel": False, "timestamp": 1},
            {
                "update_type": "message_created",
                "timestamp": 2,
                "message": {"recipient": {"chat_id": 200, "chat_type": "channel"}, "body": {}},
            },
            {
                "update_type": "message_created",
                "timestamp": 3,
                "message": {"recipient": {"chat_id": 300, "chat_type": "dialog"}, "body": {}},
            },
            {"update_type": "chat_title_changed", "chat_id": 100, "title": "Новое", "timestamp": 4},
            {"update_type": "bot_removed", "chat_id": 400},
            {"update_type": "bot_removed", "chat_id": 500},
            {"update_type": "bot_added", "chat_id": 500},
        ],
    }
    route = respx.get(f"{BASE}/updates").mock(return_value=httpx.Response(200, json=updates))
    events = await sender.poll_chats(3, 20)
    # 300 — личный диалог, не чат; 500 удалили и снова добавили — чат есть
    assert events.seen == {"100": "Новое", "200": "", "500": ""}
    assert events.removed == {"400"}
    assert events.marker == 5
    request = route.calls.last.request
    assert request.headers["Authorization"] == TOKEN
    assert request.url.params["marker"] == "3" and request.url.params["timeout"] == "20"
    assert "bot_removed" in request.url.params["types"]


@respx.mock
async def test_poll_chats_first_call_without_marker(sender: MaxSender) -> None:
    route = respx.get(f"{BASE}/updates").mock(
        return_value=httpx.Response(200, json={"updates": [], "marker": 9})
    )
    events = await sender.poll_chats(None, 0)
    assert "marker" not in route.calls.last.request.url.params
    assert events.marker == 9 and not events.seen and not events.removed


@respx.mock
async def test_poll_chats_error(sender: MaxSender) -> None:
    respx.get(f"{BASE}/updates").mock(return_value=httpx.Response(401, json={"message": "bad"}))
    with pytest.raises(SenderError, match="401"):
        await sender.poll_chats(None, 0)


@respx.mock
async def test_list_chats_checks_known(sender: MaxSender) -> None:
    respx.get(f"{BASE}/chats/100").mock(return_value=httpx.Response(200, json={"title": "Юг"}))
    respx.get(f"{BASE}/chats/200").mock(return_value=httpx.Response(403, json={}))
    respx.get(f"{BASE}/chats/300").mock(side_effect=httpx.ConnectError("x"))
    respx.get(f"{BASE}/chats/400").mock(return_value=httpx.Response(404, json={}))
    chats = await sender.list_chats(
        {"100": "старое", "200": "Север", "300": "Восток", "400": "Запад"}
    )
    # 100 — название обновилось; 200/400 — бота там нет; 300 — сбой сети, оставляем как было
    assert [(c.id, c.title) for c in chats] == [("100", "Юг"), ("300", "Восток")]
    assert await sender.list_chats() == []
