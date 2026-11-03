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
from app.senders.wa_greenapi import GreenApiSender

BASE = "https://api.green-api.com"
ID = "1101000001"
TOKEN = "green-secret-token"
PNG = b"\x89PNG\r\n\x1a\nfake"


def url(method: str) -> str:
    return f"{BASE}/waInstance{ID}/{method}/{TOKEN}"


@pytest.fixture
async def sender() -> AsyncIterator[GreenApiSender]:
    async with httpx.AsyncClient() as client:
        yield GreenApiSender(client, BASE + "/", ID, TOKEN, rng=random.Random(1))


@respx.mock
async def test_send_message(sender: GreenApiSender) -> None:
    route = respx.post(url("sendMessage")).mock(
        return_value=httpx.Response(200, json={"idMessage": "BAE5F4886F6F2D05"})
    )
    result = await sender.send_text("120363@g.us", "Текст 🔴")
    assert result.ok and result.message_id == "BAE5F4886F6F2D05"
    body = json.loads(route.calls.last.request.read())
    assert body["chatId"] == "120363@g.us" and body["message"] == "Текст 🔴"
    assert 2000 <= body["typingTime"] <= 5000


@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (466, False), (429, True), (502, True)]
)
@respx.mock
async def test_send_errors_mask_token(sender: GreenApiSender, status: int, retryable: bool) -> None:
    respx.post(url("sendMessage")).mock(
        return_value=httpx.Response(status, text=f"error for {TOKEN}")
    )
    result = await sender.send_text("1@g.us", "t")
    assert not result.ok and result.retryable is retryable
    assert result.error is not None and TOKEN not in result.error and "***" in result.error


@respx.mock
async def test_send_read_timeout_not_retried(sender: GreenApiSender) -> None:
    """Таймаут ответа: сообщение могло уйти — не повторяем, чтобы не было дубля."""
    respx.post(url("sendMessage")).mock(side_effect=httpx.ReadTimeout(f"timeout {TOKEN}"))
    result = await sender.send_text("1@g.us", "t")
    assert not result.ok and not result.retryable
    assert result.error is not None and TOKEN not in result.error
    assert texts.MAYBE_DELIVERED in result.error


@respx.mock
async def test_send_connect_error_retryable(sender: GreenApiSender) -> None:
    respx.post(url("sendMessage")).mock(side_effect=httpx.ConnectError(f"fail {TOKEN}"))
    result = await sender.send_text("1@g.us", "t")
    assert not result.ok and result.retryable
    assert result.error is not None and TOKEN not in result.error


@pytest.mark.parametrize(
    ("state", "ok"), [("authorized", True), ("notAuthorized", False), ("blocked", False)]
)
@respx.mock
async def test_health(sender: GreenApiSender, state: str, ok: bool) -> None:
    respx.get(url("getStateInstance")).mock(
        return_value=httpx.Response(200, json={"stateInstance": state})
    )
    health = await sender.health()
    assert health.ok is ok and health.detail == state


@respx.mock
async def test_qr(sender: GreenApiSender) -> None:
    respx.get(url("qr")).mock(
        return_value=httpx.Response(
            200, json={"type": "qrCode", "message": base64.b64encode(PNG).decode()}
        )
    )
    qr = await sender.get_qr()
    assert qr.png == PNG


@respx.mock
async def test_qr_already_logged(sender: GreenApiSender) -> None:
    respx.get(url("qr")).mock(
        return_value=httpx.Response(200, json={"type": "alreadyLogged", "message": "ok"})
    )
    assert (await sender.get_qr()).already_connected


@respx.mock
async def test_qr_error(sender: GreenApiSender) -> None:
    respx.get(url("qr")).mock(
        return_value=httpx.Response(200, json={"type": "error", "message": "instance not ready"})
    )
    with pytest.raises(SenderError, match="not ready"):
        await sender.get_qr()


@respx.mock
async def test_list_groups(sender: GreenApiSender) -> None:
    respx.get(url("getContacts")).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "79001234567@c.us", "name": "Иван", "type": "user"},
                {"id": "120363@g.us", "name": "Грузы", "type": "group"},
            ],
        )
    )
    groups = await sender.list_groups()
    assert [(g.id, g.title) for g in groups] == [("120363@g.us", "Грузы")]


@respx.mock
async def test_list_groups_error_masks_token(sender: GreenApiSender) -> None:
    respx.get(url("getContacts")).mock(side_effect=httpx.ConnectError(f"fail {TOKEN}"))
    with pytest.raises(SenderError) as info:
        await sender.list_groups()
    assert TOKEN not in str(info.value)
