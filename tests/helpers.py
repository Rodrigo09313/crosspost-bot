"""Мок Telegram-бота: перехватывает все вызовы API без сети."""

from __future__ import annotations

from datetime import datetime
from itertools import count
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, ChatMember, Message, MessageId, Update, User

ADMIN_ID = 111
STRANGER_ID = 999


class MockedBot(Bot):
    def __init__(self) -> None:
        super().__init__("42:TEST", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list[TelegramMethod[Any]] = []
        self._ids = count(1000)
        self.edit_error: str | None = None  # имитация ошибки при EditMessageText
        # Ответы getChat / getChatMember по id чата; None — старое поведение (вернуть True).
        self.chats: dict[int, Chat] | None = None
        self.members: dict[int, ChatMember] = {}

    async def __call__(
        self, method: TelegramMethod[Any], request_timeout: int | None = None
    ) -> Any:
        self.calls.append(method)
        name = type(method).__name__
        if name == "EditMessageText" and self.edit_error:
            raise TelegramBadRequest(method, self.edit_error)
        if name == "CopyMessage":
            return MessageId(message_id=next(self._ids))
        if name in {
            "SendMessage",
            "SendRichMessage",
            "EditMessageText",
            "SendPhoto",
            "EditMessageReplyMarkup",
        }:
            chat_id = getattr(method, "chat_id", None) or ADMIN_ID
            return Message(
                message_id=next(self._ids),
                date=datetime.now(),
                chat=Chat(id=int(chat_id), type="private"),
                text=getattr(method, "text", None),
            )
        if name in {"GetChat", "GetChatMember"} and self.chats is not None:
            chat_id = int(method.chat_id)  # type: ignore[attr-defined]
            if chat_id not in self.chats:
                raise TelegramBadRequest(method, "Bad Request: chat not found")
            if name == "GetChat":
                return self.chats[chat_id]
            return self.members[chat_id]
        if name == "GetMe":
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")
        return True

    def sent(self, name: str) -> list[Any]:
        return [c for c in self.calls if type(c).__name__ == name]

    def texts(self) -> list[str]:
        """Тексты отправленных и изменённых сообщений; rich-сообщение — одной строкой."""
        result: list[str] = []
        for c in self.calls:
            if type(c).__name__ not in {"SendMessage", "SendRichMessage", "EditMessageText"}:
                continue
            rich = getattr(c, "rich_message", None)
            result.append(rich_text(rich) if rich is not None else c.text)
        return result


def rich_text(rich: Any) -> str:
    """Rich-сообщение в строку для проверок: блоки — по строкам, ячейки таблицы — через « | »."""
    lines: list[str] = []

    def plain(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(plain(v) for v in value)
        return plain(getattr(value, "text", ""))

    def walk(blocks: list[Any]) -> None:
        for b in blocks:
            if getattr(b, "cells", None) is not None:
                lines.extend(" | ".join(plain(cell.text) for cell in row) for row in b.cells)
            elif getattr(b, "summary", None) is not None:
                lines.append(plain(b.summary))
                walk(b.blocks)
            else:
                lines.append(plain(b.text))

    walk(rich.blocks)
    return "\n".join(lines)


_update_ids = count(1)
_message_ids = count(1)


def user(uid: int) -> dict[str, Any]:
    return {"id": uid, "is_bot": False, "first_name": "U"}


def message_update(uid: int = ADMIN_ID, chat_type: str = "private", **fields: Any) -> Update:
    chat: dict[str, Any] = {"id": uid if chat_type == "private" else -100500, "type": chat_type}
    if chat_type != "private":
        chat["title"] = "Группа"
    data: dict[str, Any] = {
        "message_id": next(_message_ids),
        "date": int(datetime.now().timestamp()),
        "chat": chat,
        "from": user(uid),
        **fields,
    }
    return Update.model_validate({"update_id": next(_update_ids), "message": data})


def command_update(command: str, uid: int = ADMIN_ID, chat_type: str = "private") -> Update:
    return message_update(
        uid,
        chat_type,
        text=command,
        entities=[{"type": "bot_command", "offset": 0, "length": len(command.split()[0])}],
    )


def callback_update(data: str, message_id: int, uid: int = ADMIN_ID) -> Update:
    return Update.model_validate(
        {
            "update_id": next(_update_ids),
            "callback_query": {
                "id": str(next(_update_ids)),
                "from": user(uid),
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": int(datetime.now().timestamp()),
                    "chat": {"id": uid, "type": "private"},
                    "from": {"id": 42, "is_bot": True, "first_name": "Bot"},
                    "text": "preview",
                },
            },
        }
    )
