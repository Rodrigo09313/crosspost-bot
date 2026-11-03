"""Создание отправщиков по конфигу."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass

import httpx
from aiogram import Bot

from app.config import NETWORK_MAX, NETWORK_TG, NETWORK_WA, Settings
from app.senders.base import Sender, WhatsAppSender
from app.senders.max import MaxSender
from app.senders.telegram import TelegramSender
from app.senders.wa_evolution import EvolutionSender
from app.senders.wa_greenapi import GreenApiSender

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Senders:
    """Отправщики по сетям. MAX меняется на ходу, когда в меню подключают новый токен."""

    tg: TelegramSender
    max: MaxSender | None
    wa: WhatsAppSender | None

    def by_network(self) -> dict[str, Sender]:
        result: dict[str, Sender] = {NETWORK_TG: self.tg}
        if self.max is not None:
            result[NETWORK_MAX] = self.max
        if self.wa is not None:
            result[NETWORK_WA] = self.wa
        return result

    def live(self) -> Mapping[str, Sender]:
        """Словарь для воркера, который всегда показывает текущих отправщиков."""
        return _LiveSenders(self)


class _LiveSenders(Mapping[str, Sender]):
    def __init__(self, senders: Senders) -> None:
        self._senders = senders

    def __getitem__(self, key: str) -> Sender:
        return self._senders.by_network()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._senders.by_network())

    def __len__(self) -> int:
        return len(self._senders.by_network())


MaxFactory = Callable[[str], MaxSender]


def build_wa_sender(settings: Settings, client: httpx.AsyncClient) -> WhatsAppSender | None:
    if settings.wa_provider == "greenapi":
        if settings.greenapi_id_instance and settings.greenapi_api_token:
            return GreenApiSender(
                client,
                settings.greenapi_api_url,
                settings.greenapi_id_instance,
                settings.greenapi_api_token.get_secret_value(),
                typing=settings.wa_typing,
            )
        return None
    if settings.evolution_api_key:
        return EvolutionSender(
            client,
            settings.evolution_url,
            settings.evolution_api_key.get_secret_value(),
            settings.evolution_instance,
            typing=settings.wa_typing,
        )
    return None


def build_senders(
    settings: Settings,
    bot: Bot,
    client: httpx.AsyncClient,
    max_client: httpx.AsyncClient | None = None,
    *,
    max_token: str | None = None,
) -> Senders:
    """max_token — токен MAX из меню бота; если не задан, берётся MAX_BOT_TOKEN из .env."""
    token = max_token or (
        settings.max_bot_token.get_secret_value() if settings.max_bot_token else None
    )
    max_sender = max_factory(settings, max_client or client)(token) if token else None
    return Senders(
        tg=TelegramSender(bot, settings.tg_target_chat_id),
        max=max_sender,
        wa=build_wa_sender(settings, client),
    )


def max_factory(settings: Settings, client: httpx.AsyncClient) -> MaxFactory:
    """Создаёт отправщика MAX по токену (для подключения из меню без перезапуска)."""

    def make(token: str) -> MaxSender:
        return MaxSender(client, settings.max_api_url, token)

    return make
