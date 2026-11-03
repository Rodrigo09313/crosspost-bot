"""Настройка логирования с маскировкой секретов."""

from __future__ import annotations

import logging
from collections.abc import Iterable

MASK = "***"


class SecretMaskFilter(logging.Filter):
    """Заменяет секреты (токены, ключи) в тексте записей лога на ***."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def add(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets = sorted({*self._secrets, secret}, key=len, reverse=True)

    def mask(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        masked = self.mask(message)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.mask(record.exc_text)
        if masked != message:
            record.msg = masked
            record.args = None
        return True


_mask_filter: SecretMaskFilter | None = None


def add_secret(secret: str) -> None:
    """Маскировать в логах секрет, появившийся после запуска (токен MAX из меню)."""
    if _mask_filter is not None:
        _mask_filter.add(secret)


def setup_logging(level: str, secrets: Iterable[str]) -> None:
    global _mask_filter
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _mask_filter = SecretMaskFilter(secrets)
    handler.addFilter(_mask_filter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx пишет полный URL запроса на INFO (у GREEN-API токен в пути) — приглушаем.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
