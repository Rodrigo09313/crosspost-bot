"""Конфигурация из переменных окружения / .env (pydantic-settings)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

NETWORK_TG = "tg"
NETWORK_MAX = "max"
NETWORK_WA = "wa"
ALL_NETWORKS = (NETWORK_TG, NETWORK_MAX, NETWORK_WA)


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- Telegram ---
    tg_bot_token: SecretStr
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    tg_target_chat_id: int | None = None

    # --- MAX ---
    max_bot_token: SecretStr | None = None
    max_api_url: str = "https://platform-api2.max.ru"
    max_target_chat_id: str | None = None

    # --- WhatsApp ---
    wa_provider: Literal["evolution", "greenapi"] = "evolution"
    # Стартовый список групп; после первого выбора в меню бота группы живут в SQLite.
    wa_group_ids: Annotated[list[str], NoDecode] = Field(default_factory=list)
    wa_typing: bool = True

    evolution_url: str = "http://evolution-api:8080"
    evolution_api_key: SecretStr | None = None
    evolution_instance: str = "main"

    greenapi_api_url: str = "https://api.green-api.com"
    greenapi_id_instance: str | None = None
    greenapi_api_token: SecretStr | None = None

    # --- Прочее ---
    db_path: Path = Path("/data/bot.sqlite3")
    log_level: str = "INFO"

    @field_validator("admin_ids", "wa_group_ids", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @property
    def max_enabled(self) -> bool:
        return bool(self.max_target_chat_id) and self.max_bot_token is not None

    def secrets(self) -> list[str]:
        """Все секретные значения — для маскировки в логах."""
        values = [
            self.tg_bot_token,
            self.max_bot_token,
            self.evolution_api_key,
            self.greenapi_api_token,
        ]
        return [v.get_secret_value() for v in values if v is not None and v.get_secret_value()]
