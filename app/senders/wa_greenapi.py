"""WhatsApp через GREEN-API (облако, резерв).

Сверено с официальным клиентом green-api/whatsapp-api-client-python (10.2026):
- URL: {GREENAPI_API_URL}/waInstance{idInstance}/{method}/{apiTokenInstance};
- getStateInstance → {"stateInstance": "authorized" | "notAuthorized" | ...};
- sendMessage {"chatId", "message", "typingTime"} → {"idMessage"};
- qr → {"type": "qrCode" | "alreadyLogged" | "error", "message": base64 PNG | текст};
- getContacts → [{"id": "...@g.us", "name", "type"}]; группы отбираем по суффиксу @g.us.
Токен — часть пути, поэтому он маскируется во всех текстах ошибок.
"""

from __future__ import annotations

import base64
import random
from typing import Any

import httpx

from app.config import NETWORK_WA
from app.senders.base import (
    GROUPS_TIMEOUT,
    ChatInfo,
    HealthStatus,
    QrCode,
    SenderError,
    SendResult,
    SourceMessage,
    error_text,
    http_failure,
    json_list,
    json_object,
    transport_failure,
)

STATE_AUTHORIZED = "authorized"
TYPING_MS = (2000, 5000)


class GreenApiSender:
    network = NETWORK_WA

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        id_instance: str,
        api_token: str,
        *,
        typing: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._id = id_instance
        self._token = api_token
        self._typing = typing
        self._rng = rng or random.Random()

    def _url(self, method: str) -> str:
        return f"{self._base}/waInstance{self._id}/{method}/{self._token}"

    def _mask(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    async def _request(
        self,
        http_method: str,
        method: str,
        *,
        json: dict[str, Any] | None = None,
        http_timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                http_method,
                self._url(method),
                json=json,
                timeout=http_timeout or self._client.timeout,
            )
        except httpx.HTTPError as exc:
            raise SenderError(self._mask(f"{type(exc).__name__}: {exc}")) from exc

    def _fail(self, response: httpx.Response) -> SenderError:
        return SenderError(self._mask(f"HTTP {response.status_code}: {error_text(response)}"))

    async def state(self) -> str:
        response = await self._request("GET", "getStateInstance")
        if response.status_code != 200:
            raise self._fail(response)
        return str(json_object(response).get("stateInstance", "unknown"))

    async def health(self) -> HealthStatus:
        try:
            state = await self.state()
        except SenderError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(state == STATE_AUTHORIZED, state)

    async def is_connected(self) -> bool:
        return (await self.health()).ok

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult:
        body: dict[str, Any] = {"chatId": chat_id, "message": text}
        if self._typing:
            body["typingTime"] = self._rng.randint(*TYPING_MS)
        try:
            response = await self._request("POST", "sendMessage", json=body)
        except SenderError as exc:
            if isinstance(exc.__cause__, httpx.HTTPError):
                return transport_failure(exc.__cause__, str(exc))
            return SendResult.failure(str(exc), retryable=True)
        if response.status_code != 200:
            failure = http_failure(response, error_text(response))
            return SendResult.failure(
                self._mask(failure.error or ""),
                retryable=failure.retryable,
                retry_after=failure.retry_after,
            )
        try:
            message_id = response.json()["idMessage"]
        except (ValueError, KeyError, TypeError):
            message_id = None
        return SendResult.success(str(message_id) if message_id is not None else None)

    async def get_qr(self) -> QrCode:
        response = await self._request("GET", "qr")
        if response.status_code != 200:
            raise self._fail(response)
        data = json_object(response)
        kind = data.get("type")
        if kind == "alreadyLogged":
            return QrCode(already_connected=True)
        if kind == "qrCode" and data.get("message"):
            return QrCode(png=base64.b64decode(data["message"]))
        raise SenderError(self._mask(str(data.get("message") or "QR-код недоступен")))

    async def list_groups(self) -> list[ChatInfo]:
        response = await self._request("GET", "getContacts", http_timeout=GROUPS_TIMEOUT)
        if response.status_code != 200:
            raise self._fail(response)
        return [
            ChatInfo(str(c["id"]), str(c.get("name") or c.get("contactName") or ""))
            for c in json_list(response)
            if isinstance(c, dict) and str(c.get("id", "")).endswith("@g.us")
        ]
