"""Отправка в MAX через официальный Bot API.

Сверено с официальным клиентом max-messenger/max-bot-api-client-ts (v1.0.2, 10.2026):
- базовый URL: https://platform-api2.max.ru (platform-api.max.ru помечен как устаревший);
- авторизация: заголовок `Authorization: <token>` (без «Bearer»);
- отправка: POST /messages?chat_id=<id>, тело {"text": "..."} → {"message": {"body": {"mid"}}};
- GET /chats удалён («мёртвый эндпоинт», с 06.2026), поэтому список чатов бот собирает сам:
  фоновый MaxChatWatcher слушает GET /updates (bot_added / bot_removed / message_created /
  chat_title_changed) и сохраняет чаты в SQLite; «Обновить» проверяет их GET /chats/{id}.
  События отдаются один раз; bot_added при добавлении админом на проде не приходил
  (02.11.2026) — чат замечается по первому сообщению в нём.

Сертификат platform-api2.max.ru выдан Russian Trusted Sub CA (Минцифры), корня которого
нет в certifi: без него каждый запрос падает с CERTIFICATE_VERIFY_FAILED (проверено на Pi
30.10.2026). Корень лежит в app/certs и подключается только к клиенту MAX.
"""

from __future__ import annotations

import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import certifi
import httpx

from app.config import NETWORK_MAX
from app.senders.base import (
    HTTP_TIMEOUT,
    ChatInfo,
    HealthStatus,
    SenderError,
    SendResult,
    SourceMessage,
    error_text,
    http_failure,
    json_object,
    transport_failure,
)

UPDATE_TYPES = "bot_added,bot_removed,message_created,chat_title_changed"


@dataclass(slots=True)
class ChatEvents:
    """Итог одного опроса: новый marker, замеченные чаты {id: название} и удаления."""

    marker: int | None
    seen: dict[str, str] = field(default_factory=dict)
    removed: set[str] = field(default_factory=set)

    def add(self, update: dict[str, Any]) -> None:
        kind = update.get("update_type")
        chat_id: object = None
        title: object = None
        if kind in {"bot_added", "chat_title_changed", "bot_removed"}:
            chat_id = update.get("chat_id")
            title = update.get("title")
        elif kind == "message_created":
            recipient = (update.get("message") or {}).get("recipient") or {}
            if recipient.get("chat_type") in {"chat", "channel"}:
                chat_id = recipient.get("chat_id")
        if chat_id is None:
            return
        cid = str(chat_id)
        # События идут по порядку: удалили, а потом снова добавили — чат есть.
        if kind == "bot_removed":
            self.seen.pop(cid, None)
            self.removed.add(cid)
        else:
            self.removed.discard(cid)
            self.seen[cid] = str(title or self.seen.get(cid) or "")


RUSSIAN_ROOT_CA = Path(__file__).resolve().parent.parent / "certs" / "russian_trusted_root_ca.pem"


def make_max_http_client() -> httpx.AsyncClient:
    """Клиент для MAX: certifi + корень Минцифры. Остальные сети этому корню не доверяют."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=RUSSIAN_ROOT_CA)
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, verify=context)


class MaxSender:
    network = NETWORK_MAX

    def __init__(self, client: httpx.AsyncClient, base_url: str, token: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": token}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return await self._client.request(
            method, f"{self._base}{path}", params=params, json=json, headers=self._headers
        )

    async def health(self) -> HealthStatus:
        try:
            response = await self._request("GET", "/me")
        except httpx.HTTPError as exc:
            return HealthStatus(False, f"{type(exc).__name__}: {exc}")
        if response.status_code != 200:
            return HealthStatus(False, f"HTTP {response.status_code}: {error_text(response)}")
        try:
            data = json_object(response)
        except SenderError as exc:
            return HealthStatus(False, str(exc))
        name = data.get("username") or data.get("first_name") or data.get("name") or ""
        return HealthStatus(True, str(name))

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult:
        try:
            response = await self._request(
                "POST", "/messages", params={"chat_id": chat_id}, json={"text": text}
            )
        except httpx.HTTPError as exc:
            return transport_failure(exc)
        if response.status_code != 200:
            return http_failure(response, error_text(response))
        try:
            mid = response.json()["message"]["body"]["mid"]
        except (ValueError, KeyError, TypeError):
            mid = None
        return SendResult.success(str(mid) if mid is not None else None)

    async def poll_chats(self, marker: int | None, wait_s: int) -> ChatEvents:
        """Один long polling GET /updates: какие чаты бот увидел и откуда его удалили.

        События MAX отдаются один раз — их забирает только фоновый MaxChatWatcher и сразу
        сохраняет. marker из ответа передаётся в следующий вызов.
        """
        params: dict[str, Any] = {"limit": 100, "timeout": wait_s, "types": UPDATE_TYPES}
        if marker is not None:
            params["marker"] = marker
        try:
            response = await self._request("GET", "/updates", params=params)
        except httpx.HTTPError as exc:
            raise SenderError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            raise SenderError(f"HTTP {response.status_code}: {error_text(response)}")
        data = json_object(response)
        new_marker = data.get("marker")
        events = ChatEvents(int(new_marker) if new_marker is not None else marker)
        updates = data.get("updates")
        for update in updates if isinstance(updates, list) else []:
            if isinstance(update, dict):
                events.add(update)
        return events

    async def list_chats(self, known: Mapping[str, str] | None = None) -> list[ChatInfo]:
        """Из известных чатов ({id: название}) — те, где бот сейчас есть, со свежим названием.

        Каждый проверяется GET /chats/{id}: 403/404 — бота там нет; сетевой сбой —
        оставляем как есть.
        """
        chats: list[ChatInfo] = []
        for cid, title in (known or {}).items():
            present, fresh = await self._chat_state(cid)
            if present:
                chats.append(ChatInfo(cid, fresh or title))
        return chats

    async def _chat_state(self, chat_id: str) -> tuple[bool, str | None]:
        """(бот в чате, актуальное название). Не знаем точно — считаем, что в чате."""
        try:
            response = await self._request("GET", f"/chats/{chat_id}")
        except httpx.HTTPError:
            return True, None
        if response.status_code in {403, 404}:
            return False, None
        if response.status_code != 200:
            return True, None
        try:
            title = json_object(response).get("title")
        except SenderError:
            return True, None
        return True, str(title) if title else None
