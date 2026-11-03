from __future__ import annotations

import base64
import json
import random
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from app import texts
from app.senders.base import SenderError
from app.senders.wa_evolution import EvolutionSender

BASE = "http://evolution-api:8080"
KEY = "evo-key"
PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def make(client: httpx.AsyncClient, typing: bool = True) -> EvolutionSender:
    return EvolutionSender(
        client, BASE, KEY, "main", typing=typing, rng=random.Random(1), qr_interval=0
    )


@respx.mock
async def test_send_text_with_typing(client: httpx.AsyncClient) -> None:
    route = respx.post(f"{BASE}/message/sendText/main").mock(
        return_value=httpx.Response(201, json={"key": {"id": "3EB0ABC", "remoteJid": "1@g.us"}})
    )
    result = await make(client).send_text("1203@g.us", "Текст")
    assert result.ok and result.message_id == "3EB0ABC"
    request = route.calls.last.request
    assert request.headers["apikey"] == KEY
    body = json.loads(request.read())
    assert body["number"] == "1203@g.us" and body["text"] == "Текст"
    assert 2000 <= body["delay"] <= 5000


@respx.mock
async def test_send_text_without_typing(client: httpx.AsyncClient) -> None:
    route = respx.post(f"{BASE}/message/sendText/main").mock(
        return_value=httpx.Response(201, json={"key": {"id": "X"}})
    )
    await make(client, typing=False).send_text("1@g.us", "t")
    assert "delay" not in json.loads(route.calls.last.request.read())


@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (404, False), (429, True), (500, True)]
)
@respx.mock
async def test_send_text_errors(client: httpx.AsyncClient, status: int, retryable: bool) -> None:
    respx.post(f"{BASE}/message/sendText/main").mock(
        return_value=httpx.Response(
            status,
            json={
                "status": status,
                "error": "Bad Request",
                "response": {"message": ["not a group"]},
            },
        )
    )
    result = await make(client).send_text("1@g.us", "t")
    assert not result.ok and result.retryable is retryable
    assert result.error is not None and "not a group" in result.error


@respx.mock
async def test_send_text_connection_refused(client: httpx.AsyncClient) -> None:
    respx.post(f"{BASE}/message/sendText/main").mock(side_effect=httpx.ConnectError("refused"))
    result = await make(client).send_text("1@g.us", "t")
    assert not result.ok and result.retryable


@respx.mock
async def test_send_text_read_timeout_not_retried(client: httpx.AsyncClient) -> None:
    """Evolution не ответил за 30 с: сообщение могло уйти в группу — без повтора."""
    respx.post(f"{BASE}/message/sendText/main").mock(side_effect=httpx.ReadTimeout("timeout"))
    result = await make(client).send_text("1@g.us", "t")
    assert not result.ok and not result.retryable
    assert result.error is not None and texts.MAYBE_DELIVERED in result.error


@pytest.mark.parametrize(("state", "ok"), [("open", True), ("connecting", False), ("close", False)])
@respx.mock
async def test_health_states(client: httpx.AsyncClient, state: str, ok: bool) -> None:
    respx.get(f"{BASE}/instance/connectionState/main").mock(
        return_value=httpx.Response(
            200, json={"instance": {"instanceName": "main", "state": state}}
        )
    )
    sender = make(client)
    health = await sender.health()
    assert health.ok is ok and health.detail == state
    assert await sender.is_connected() is ok


@respx.mock
async def test_health_unreachable(client: httpx.AsyncClient) -> None:
    respx.get(f"{BASE}/instance/connectionState/main").mock(
        side_effect=httpx.ConnectError("no route")
    )
    health = await make(client).health()
    assert not health.ok and "недоступен" in health.detail


@respx.mock
async def test_qr_creates_instance_when_missing(client: httpx.AsyncClient) -> None:
    respx.get(f"{BASE}/instance/connectionState/main").mock(
        return_value=httpx.Response(404, json={"response": {"message": ["does not exist"]}})
    )
    create = respx.post(f"{BASE}/instance/create").mock(
        return_value=httpx.Response(201, json={"instance": {"instanceName": "main"}})
    )
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    connect = respx.get(f"{BASE}/instance/connect/main")
    connect.side_effect = [
        httpx.Response(200, json={"count": 0}),  # QR ещё генерируется
        httpx.Response(200, json={"code": "2@...", "base64": data_url, "count": 1}),
    ]
    qr = await make(client).get_qr()
    assert qr.png == PNG and not qr.already_connected
    assert json.loads(create.calls.last.request.read()) == {
        "instanceName": "main",
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    }


@respx.mock
async def test_qr_already_connected(client: httpx.AsyncClient) -> None:
    respx.get(f"{BASE}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "open"}})
    )
    connect = respx.get(f"{BASE}/instance/connect/main")
    qr = await make(client).get_qr()
    assert qr.already_connected and qr.png is None
    assert not connect.called


@respx.mock
async def test_qr_not_ready(client: httpx.AsyncClient) -> None:
    respx.get(f"{BASE}/instance/connectionState/main").mock(
        return_value=httpx.Response(200, json={"instance": {"state": "close"}})
    )
    connect = respx.get(f"{BASE}/instance/connect/main").mock(
        return_value=httpx.Response(200, json={"count": 0})
    )
    with pytest.raises(SenderError):
        await make(client).get_qr()
    assert connect.call_count == 5


@respx.mock
async def test_list_groups(client: httpx.AsyncClient) -> None:
    route = respx.get(f"{BASE}/group/fetchAllGroups/main").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "1203@g.us", "subject": "Грузы Юг", "size": 50},
                {"id": "1204@g.us", "subject": None},
            ],
        )
    )
    groups = await make(client).list_groups()
    assert [(g.id, g.title) for g in groups] == [("1203@g.us", "Грузы Юг"), ("1204@g.us", "")]
    assert route.calls.last.request.url.params["getParticipants"] == "false"
    assert route.calls.last.request.headers["apikey"] == KEY


@respx.mock
async def test_list_groups_waits_long(client: httpx.AsyncClient) -> None:
    """У номера с ~300 группами Evolution отвечает ~95 с — обычных 30 с мало."""
    route = respx.get(f"{BASE}/group/fetchAllGroups/main").mock(
        return_value=httpx.Response(200, json=[])
    )
    await make(client).list_groups()
    assert route.calls.last.request.extensions["timeout"]["read"] == 180.0
