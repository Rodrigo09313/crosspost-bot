"""Точка входа: python -m app.main"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from app.bot.keyboards import progress_kb
from app.bot.setup import build_dispatcher, setup_bot_commands
from app.config import Settings
from app.logging_setup import add_secret, setup_logging
from app.max_watch import MaxChatWatcher
from app.runtime import RuntimeConfig
from app.senders.base import make_http_client
from app.senders.factory import build_senders, max_factory
from app.senders.max import make_max_http_client
from app.storage import HISTORY_KEEP_DAYS, Storage
from app.worker import Worker

log = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    storage = await Storage.open(settings.db_path)
    pruned = await storage.prune(HISTORY_KEEP_DAYS)
    if pruned:
        log.info("Удалено старых рассылок: %s", pruned)
    bot = Bot(
        settings.tg_bot_token.get_secret_value(),
        session=AiohttpSession(timeout=30),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    http = make_http_client()
    max_http = make_max_http_client()
    runtime = RuntimeConfig(storage, settings)
    max_token = await runtime.max_token()
    if max_token:
        add_secret(max_token)
    senders = build_senders(settings, bot, http, max_http, max_token=max_token)
    worker = Worker(
        storage, bot, senders.live(), delay=runtime.delay_range, progress_kb=progress_kb
    )
    max_watcher = MaxChatWatcher(senders, runtime)
    dp = build_dispatcher(
        settings,
        db=storage,
        queue=worker,
        senders=senders,
        runtime=runtime,
        wa_titles=runtime.wa_catalog_titles,
        max_factory=max_factory(settings, max_http),
    )
    await setup_bot_commands(bot)
    worker.start()
    max_watcher.start()
    try:
        log.info("Запуск polling")
        await dp.start_polling(bot, handle_signals=True)
    finally:
        await max_watcher.shutdown()
        await worker.shutdown()
        await bot.session.close()
        await http.aclose()
        await max_http.aclose()
        await storage.close()


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, settings.secrets())
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
