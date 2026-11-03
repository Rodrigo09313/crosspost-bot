"""WhatsApp через Evolution API v2 (self-hosted, Baileys).

Сверено с исходниками EvolutionAPI/evolution-api, тег 2.3.7:
- авторизация: заголовок `apikey`;
- POST /instance/create {"instanceName", "integration": "WHATSAPP-BAILEYS", "qrcode": true};
- GET /instance/connect/{instance} → {"base64": "data:image/png;base64,...", "code", ...};
- GET /instance/connectionState/{instance} → {"instance": {"state": "open"|"connecting"|"close"}};
- GET /group/fetchAllGroups/{instance}?getParticipants=false → [{"id", "subject", ...}];
- POST /message/sendText/{instance} {"number", "text", "delay"} → {"key": {"id"}}.
  Поле delay (мс) включает статус «печатает» на это время перед отправкой —
  отдельный /chat/sendPresence не нужен.
"""

from __future__ import annotations

import asyncio
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

STATE_OPEN = "open"
STATE_NOT_CREATED = "not_created"
TYPING_MS = (2000, 5000)


def decode_data_url(value: str) -> bytes:
    """«data:image/png;base64,....» или «голый» base64 → байты."""
    if value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


class EvolutionSender:
    network = NETWORK_WA

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        instance: str,
        *,
        typing: bool = True,
        rng: random.Random | None = None,
        qr_attempts: int = 5,
        qr_interval: float = 2.0,
    ) -> None:
        self._client = client
        self._qr_attempts = qr_attempts
        self._qr_interval = qr_interval
        self._base = base_url.rstrip("/")
        self._headers = {"apikey": api_key}
        self._instance = instance
        self._typing = typing
        self._rng = rng or random.Random()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        http_timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        return await self._client.request(
            method,
            f"{self._base}{path}",
            params=params,
            json=json,
            headers=self._headers,
            timeout=http_timeout or self._client.timeout,
        )

    async def state(self) -> str:
        try:
            response = await self._request("GET", f"/instance/connectionState/{self._instance}")
        except httpx.HTTPError as exc:
            raise SenderError(f"Evolution API недоступен: {type(exc).__name__}: {exc}") from exc
        if response.status_code == 404:
            return STATE_NOT_CREATED
        if response.status_code != 200:
            raise SenderError(f"HTTP {response.status_code}: {error_text(response)}")
        instance = json_object(response).get("instance")
        return str(instance.get("state", "unknown")) if isinstance(instance, dict) else "unknown"

    async def health(self) -> HealthStatus:
        try:
            state = await self.state()
        except SenderError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(state == STATE_OPEN, state)

    async def is_connected(self) -> bool:
        return (await self.health()).ok

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult:
        body: dict[str, Any] = {"number": chat_id, "text": text}
        if self._typing:
            body["delay"] = self._rng.randint(*TYPING_MS)
        try:
            response = await self._request("POST", f"/message/sendText/{self._instance}", json=body)
        except httpx.HTTPError as exc:
            return transport_failure(exc)
        if response.status_code not in (200, 201):
            return http_failure(response, error_text(response))
        try:
            message_id = response.json()["key"]["id"]
        except (ValueError, KeyError, TypeError):
            message_id = None
        return SendResult.success(str(message_id) if message_id is not None else None)

    async def get_qr(self) -> QrCode:
        state = await self.state()
        if state == STATE_OPEN:
            return QrCode(already_connected=True)
        if state == STATE_NOT_CREATED:
            await self._create_instance()
        # Сразу после создания/переподключения QR появляется через пару секунд.
        for attempt in range(self._qr_attempts):
            if attempt:
                await asyncio.sleep(self._qr_interval)
            data = await self._connect()
            if data.get("instance", {}).get("state") == STATE_OPEN:
                return QrCode(already_connected=True)
            qr = data.get("base64") or (data.get("qrcode") or {}).get("base64")
            if qr:
                return QrCode(png=decode_data_url(qr))
        raise SenderError("QR-код ещё не готов, повторите /wa_qr через минуту")

    async def _connect(self) -> dict[str, Any]:
        try:
            response = await self._request("GET", f"/instance/connect/{self._instance}")
        except httpx.HTTPError as exc:
            raise SenderError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise SenderError(f"HTTP {response.status_code}: {error_text(response)}")
        return json_object(response)

    async def _create_instance(self) -> None:
        body = {"instanceName": self._instance, "integration": "WHATSAPP-BAILEYS", "qrcode": True}
        try:
            response = await self._request("POST", "/instance/create", json=body)
        except httpx.HTTPError as exc:
            raise SenderError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code not in (200, 201):
            raise SenderError(f"HTTP {response.status_code}: {error_text(response)}")

    async def list_groups(self) -> list[ChatInfo]:
        try:
            response = await self._request(
                "GET",
                f"/group/fetchAllGroups/{self._instance}",
                params={"getParticipants": "false"},
                http_timeout=GROUPS_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise SenderError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise SenderError(f"HTTP {response.status_code}: {error_text(response)}")
        return [
            ChatInfo(str(g["id"]), str(g.get("subject") or ""))
            for g in json_list(response)
            if isinstance(g, dict) and g.get("id")
        ]
