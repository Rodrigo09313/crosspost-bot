"""Единый интерфейс отправщика и общие типы."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from app import texts

# Таймауты сетевых вызовов: 10 с на соединение, 30 с на чтение.
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)
# Список групп WhatsApp: у номера с ~300 группами Evolution отвечает ~95 с (прод, 02.11.2026).
GROUPS_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """Исходное сообщение админа (для copy_message в Telegram)."""

    chat_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    message_id: str | None = None
    error: str | None = None
    retryable: bool = False
    retry_after: float | None = None

    @classmethod
    def success(cls, message_id: str | None) -> SendResult:
        return cls(ok=True, message_id=message_id)

    @classmethod
    def failure(
        cls, error: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> SendResult:
        return cls(ok=False, error=error, retryable=retryable, retry_after=retry_after)


@dataclass(frozen=True, slots=True)
class HealthStatus:
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ChatInfo:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class QrCode:
    """PNG с QR-кодом или признак, что номер уже привязан."""

    png: bytes | None = None
    already_connected: bool = False


@runtime_checkable
class Sender(Protocol):
    network: str  # "tg" | "max" | "wa"

    async def health(self) -> HealthStatus: ...

    async def send_text(
        self, chat_id: str, text: str, *, source: SourceMessage | None = None
    ) -> SendResult: ...


class WhatsAppSender(Sender, Protocol):
    """Отправщик WhatsApp: дополнительно QR-код и список групп."""

    async def is_connected(self) -> bool: ...

    async def get_qr(self) -> QrCode: ...

    async def list_groups(self) -> list[ChatInfo]: ...


class SenderError(Exception):
    """Ошибка сервисного запроса (список чатов, QR и т. п.)."""


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT)


def http_failure(response: httpx.Response, error: str) -> SendResult:
    """429 и 5xx — временные ошибки (повторяем), остальные 4xx — нет."""
    status = response.status_code
    retryable = status == 429 or status >= 500
    retry_after: float | None = None
    header = response.headers.get("Retry-After")
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            retry_after = None
    return SendResult.failure(
        f"HTTP {status}: {error}", retryable=retryable, retry_after=retry_after
    )


# Ошибки, при которых запрос точно не дошёл до сервера: повтор безопасен. При остальных
# (таймаут чтения, обрыв ответа) сообщение могло уйти, и повтор дал бы дубль в группе.
NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def transport_failure(exc: httpx.HTTPError, error: str | None = None) -> SendResult:
    error = error or f"{type(exc).__name__}: {exc}"
    if isinstance(exc, NOT_SENT_ERRORS):
        return SendResult.failure(error, retryable=True)
    return SendResult.failure(f"{error}; {texts.MAYBE_DELIVERED}")


def error_text(response: httpx.Response, limit: int = 300) -> str:
    """Короткое описание ошибки из тела ответа."""
    try:
        data = response.json()
    except ValueError:
        return response.text[:limit] or response.reason_phrase
    if isinstance(data, dict):
        nested = data.get("response")
        if isinstance(nested, dict) and nested.get("message"):
            return str(nested["message"])[:limit]
        for key in ("message", "error", "description"):
            value = data.get(key)
            if value:
                return str(value)[:limit]
    return str(data)[:limit]


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        snippet = response.text[:100] or response.reason_phrase
        raise SenderError(f"HTTP {response.status_code}: {texts.BAD_RESPONSE}: {snippet}") from exc


def json_object(response: httpx.Response) -> dict[str, Any]:
    """Тело ответа как JSON-объект; иначе SenderError с понятным текстом (а не падение)."""
    data = _json(response)
    if not isinstance(data, dict):
        raise SenderError(f"HTTP {response.status_code}: {texts.BAD_RESPONSE}")
    return data


def json_list(response: httpx.Response) -> list[Any]:
    """Тело ответа как JSON-массив; иначе SenderError."""
    data = _json(response)
    if not isinstance(data, list):
        raise SenderError(f"HTTP {response.status_code}: {texts.BAD_RESPONSE}")
    return data
