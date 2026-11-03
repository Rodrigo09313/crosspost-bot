"""Сборка Dispatcher: роутеры, фильтр админов, зависимости."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from app import texts
from app.bot.drafts import AlbumTracker, DraftStore
from app.bot.errors import on_error
from app.bot.handlers import setup_routers
from app.bot.menu import MaxCatalog, PendingInput, Screens, WaCatalog
from app.bot.middlewares import AdminOnlyMiddleware
from app.config import Settings
from app.runtime import RuntimeConfig

log = logging.getLogger(__name__)


def build_dispatcher(settings: Settings, **deps: Any) -> Dispatcher:
    """deps попадают в обработчики по имени аргумента (db, queue, wa_titles, runtime, ...)."""
    if "runtime" not in deps and "db" in deps:
        deps["runtime"] = RuntimeConfig(deps["db"], settings)
    dp = Dispatcher(
        settings=settings,
        drafts=DraftStore(),
        albums=AlbumTracker(),
        wa_catalog=WaCatalog(),
        max_catalog=MaxCatalog(),
        pending=PendingInput(),
        screens=Screens(),
        **deps,
    )
    dp.update.outer_middleware(AdminOnlyMiddleware(settings.admin_ids))
    dp.include_routers(*setup_routers())
    dp.errors.register(on_error)
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    """Синяя кнопка «Меню» у поля ввода: список команд в личке с ботом."""
    commands = [BotCommand(command=c, description=d) for c, d in texts.BOT_COMMANDS]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
    except TelegramAPIError as exc:
        # Не критично: нижнее меню и команды работают и без этого списка.
        log.warning("Не удалось задать список команд: %s", exc.message)
