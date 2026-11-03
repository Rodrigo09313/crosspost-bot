"""Настройки, которые админ меняет из меню бота: чаты Telegram, группы WhatsApp и паузы.

Хранятся в SQLite (таблица settings) и переживают перезапуск. TG_TARGET_CHAT_ID и
WA_GROUP_IDS из .env — только стартовые значения: используются, пока список ни разу
не меняли в меню.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import Settings
from app.storage import Storage

KEY_WA_GROUPS = "wa_groups"
KEY_WA_DELAY = "wa_delay"
KEY_TG_CHATS = "tg_chats"
KEY_TG_KNOWN = "tg_known"  # группы и каналы TG, куда бота добавили: {id: название}
KEY_MAX_TOKEN = "max_token"
# последний список групп номера: {"at": ISO, "groups": {id: название}}
KEY_WA_CATALOG = "wa_catalog"
KEY_MAX_CHATS = "max_chats"
KEY_MAX_KNOWN = "max_known"  # все чаты MAX, где бот был замечен: {id: название}


@dataclass(frozen=True, slots=True)
class DelayPreset:
    key: str
    label: str
    min_s: float
    max_s: float

    @property
    def avg_s(self) -> float:
        return (self.min_s + self.max_s) / 2


DELAY_PRESETS = (
    # Пауза перед каждой группой — случайная в диапазоне (random.uniform в воркере).
    # Один режим на все сети: Telegram, MAX и WhatsApp.
    DelayPreset("5-15s", "5–15 с", 5, 15),
    DelayPreset("10-30s", "10–30 с", 10, 30),
    DelayPreset("20-60s", "20–60 с", 20, 60),
    DelayPreset("1-3m", "1–3 мин", 60, 180),
)
# Сохранённый режим, которого больше нет в списке (1–10, 3–5, 5–30 мин), сменяется на этот.
DEFAULT_DELAY = "10-30s"
_PRESETS = {p.key: p for p in DELAY_PRESETS}


@dataclass(frozen=True, slots=True)
class Targets:
    """Куда рассылать сейчас: {id: название} по каждой сети."""

    tg: dict[str, str] = field(default_factory=dict)
    max: dict[str, str] = field(default_factory=dict)  # пусто, если MAX не подключён
    wa: dict[str, str] = field(default_factory=dict)


class RuntimeConfig:
    """Кэш в памяти поверх таблицы settings; читается из БД при первом обращении."""

    def __init__(self, storage: Storage, settings: Settings) -> None:
        self._storage = storage
        self._seed_groups = list(settings.wa_group_ids)
        self._seed_tg = settings.tg_target_chat_id
        self._seed_max_token = (
            settings.max_bot_token.get_secret_value() if settings.max_bot_token else None
        )
        self._seed_max_chat = settings.max_target_chat_id
        self._max_token: str | None = None
        self._max_token_loaded = False
        self._max_chats: dict[str, str] | None = None
        self._groups: dict[str, str] | None = None
        self._tg_chats: dict[str, str] | None = None
        self._delay: DelayPreset | None = None

    async def targets(self) -> Targets:
        max_chats = await self.max_chats() if await self.max_token() else {}
        return Targets(tg=await self.tg_chats(), max=max_chats, wa=await self.wa_groups())

    # --- последний полученный список групп WhatsApp (кэш: запрос идёт до ~1,5 мин) ---

    async def wa_catalog(self) -> tuple[dict[str, str], datetime] | None:
        raw = await self._storage.get_setting(KEY_WA_CATALOG)
        if raw is None:
            return None
        data = json.loads(raw)
        groups = {str(k): str(v) for k, v in data.get("groups", {}).items()}
        return groups, datetime.fromisoformat(data["at"])

    async def wa_catalog_titles(self) -> dict[str, str]:
        """Названия групп из сохранённого списка (без запроса к WhatsApp)."""
        cached = await self.wa_catalog()
        return {gid: title for gid, title in cached[0].items() if title} if cached else {}

    async def save_wa_catalog(self, groups: dict[str, str]) -> None:
        at = datetime.now(UTC).isoformat(timespec="seconds")
        payload = {"at": at, "groups": groups}
        await self._storage.set_setting(KEY_WA_CATALOG, json.dumps(payload, ensure_ascii=False))

    # --- MAX: токен бота и выбранные чаты ---

    async def max_token(self) -> str | None:
        if not self._max_token_loaded:
            self._max_token = await self._storage.get_setting(KEY_MAX_TOKEN) or self._seed_max_token
            self._max_token_loaded = True
        return self._max_token

    async def set_max_token(self, token: str) -> None:
        if token != await self.max_token():
            await self.set_max_known({})  # другой бот — чужой список чатов не нужен
        await self._storage.set_setting(KEY_MAX_TOKEN, token)
        self._max_token, self._max_token_loaded = token, True

    async def _max_known_raw(self) -> dict[str, str]:
        raw = await self._storage.get_setting(KEY_MAX_KNOWN)
        return {str(k): str(v) for k, v in json.loads(raw).items()} if raw else {}

    async def max_known(self) -> dict[str, str]:
        """Чаты MAX, где бот был замечен, вместе с выбранными — кандидаты для проверки."""
        return {**await self.max_chats(), **await self._max_known_raw()}

    async def remember_max_chats(self, seen: dict[str, str], removed: set[str]) -> None:
        """Записать события MAX: замеченные чаты добавить, удалённые убрать."""
        known = await self._max_known_raw()
        updated = {cid: t for cid, t in known.items() if cid not in removed}
        for cid, title in seen.items():
            updated[cid] = title or updated.get(cid) or ""
        if updated != known:
            await self.set_max_known(updated)

    async def set_max_known(self, chats: dict[str, str]) -> None:
        await self._storage.set_setting(KEY_MAX_KNOWN, json.dumps(chats, ensure_ascii=False))

    async def max_chats(self) -> dict[str, str]:
        if self._max_chats is None:
            raw = await self._storage.get_setting(KEY_MAX_CHATS)
            if raw is not None:
                self._max_chats = {str(k): str(v) for k, v in json.loads(raw).items()}
            elif self._seed_max_chat:
                self._max_chats = {self._seed_max_chat: "MAX"}
            else:
                self._max_chats = {}
        return dict(self._max_chats)

    async def set_max_chat(self, chat_id: str, title: str, *, selected: bool) -> None:
        chats = await self.max_chats()
        if selected:
            chats[chat_id] = title or chats.get(chat_id) or chat_id
        else:
            chats.pop(chat_id, None)
        await self._save_max(chats)

    async def update_max_titles(self, titles: dict[str, str]) -> None:
        chats = await self.max_chats()
        changed = {
            cid: titles[cid] for cid in chats if titles.get(cid) and titles[cid] != chats[cid]
        }
        if changed:
            chats.update(changed)
            await self._save_max(chats)

    async def _save_max(self, chats: dict[str, str]) -> None:
        await self._storage.set_setting(KEY_MAX_CHATS, json.dumps(chats, ensure_ascii=False))
        self._max_chats = chats

    # --- чаты Telegram (каналы и группы): {id: название}, в порядке добавления ---

    async def tg_chats(self) -> dict[str, str]:
        if self._tg_chats is None:
            raw = await self._storage.get_setting(KEY_TG_CHATS)
            if raw is not None:
                self._tg_chats = {str(k): str(v) for k, v in json.loads(raw).items()}
            elif self._seed_tg is not None:
                self._tg_chats = {str(self._seed_tg): "Telegram"}
            else:
                self._tg_chats = {}
        return dict(self._tg_chats)

    async def tg_chat_ids(self) -> list[str]:
        return list(await self.tg_chats())

    async def add_tg_chat(self, chat_id: str, title: str) -> bool:
        """Добавляет чат или обновляет его название. True — чат новый."""
        chats = await self.tg_chats()
        new = chat_id not in chats
        chats[chat_id] = title or chats.get(chat_id) or chat_id
        await self._save_tg(chats)
        return new

    async def remove_tg_chat(self, chat_id: str) -> str | None:
        """Убирает чат из рассылки. Возвращает его название или None, если его не было."""
        chats = await self.tg_chats()
        title = chats.pop(chat_id, None)
        if title is not None:
            await self._save_tg(chats)
        return title

    async def update_tg_titles(self, titles: dict[str, str]) -> None:
        """Обновить названия выбранных чатов (их могли переименовать в Telegram)."""
        chats = await self.tg_chats()
        changed = {
            cid: titles[cid] for cid in chats if titles.get(cid) and titles[cid] != chats[cid]
        }
        if changed:
            chats.update(changed)
            await self._save_tg(chats)

    async def tg_known(self) -> dict[str, str]:
        """Группы и каналы, где бот сейчас есть (по событиям «бота добавили/убрали»)."""
        raw = await self._storage.get_setting(KEY_TG_KNOWN)
        return {str(k): str(v) for k, v in json.loads(raw).items()} if raw else {}

    async def remember_tg_chat(self, chat_id: str, title: str) -> bool:
        """Запомнить чат, куда добавили бота. True — раньше его не знали."""
        known = await self.tg_known()
        new = chat_id not in known
        known[chat_id] = title or known.get(chat_id) or chat_id
        await self._storage.set_setting(KEY_TG_KNOWN, json.dumps(known, ensure_ascii=False))
        return new

    async def forget_tg_chat(self, chat_id: str) -> None:
        """Бота убрали из чата: из списка известных (выбранный чат остаётся до «Убрать»)."""
        known = await self.tg_known()
        if known.pop(chat_id, None) is not None:
            await self._storage.set_setting(KEY_TG_KNOWN, json.dumps(known, ensure_ascii=False))

    async def _save_tg(self, chats: dict[str, str]) -> None:
        await self._storage.set_setting(KEY_TG_CHATS, json.dumps(chats, ensure_ascii=False))
        self._tg_chats = chats

    # --- группы WhatsApp: {id: название}, в порядке добавления ---

    async def wa_groups(self) -> dict[str, str]:
        if self._groups is None:
            raw = await self._storage.get_setting(KEY_WA_GROUPS)
            if raw is None:
                self._groups = {gid: gid for gid in self._seed_groups}
            else:
                self._groups = {str(k): str(v) for k, v in json.loads(raw).items()}
        return dict(self._groups)

    async def wa_group_ids(self) -> list[str]:
        return list(await self.wa_groups())

    async def set_wa_group(self, group_id: str, title: str, *, selected: bool) -> None:
        groups = await self.wa_groups()
        if selected:
            groups[group_id] = title or groups.get(group_id) or group_id
        else:
            groups.pop(group_id, None)
        await self._save_groups(groups)

    async def update_titles(self, titles: dict[str, str]) -> None:
        """Обновить названия выбранных групп (их могли переименовать в WhatsApp)."""
        groups = await self.wa_groups()
        changed = {
            gid: titles[gid] for gid in groups if titles.get(gid) and titles[gid] != groups[gid]
        }
        if changed:
            groups.update(changed)
            await self._save_groups(groups)

    async def _save_groups(self, groups: dict[str, str]) -> None:
        await self._storage.set_setting(KEY_WA_GROUPS, json.dumps(groups, ensure_ascii=False))
        self._groups = groups

    # --- пауза между чатами (все сети) ---

    async def delay(self) -> DelayPreset:
        if self._delay is None:
            key = await self._storage.get_setting(KEY_WA_DELAY)
            self._delay = _PRESETS.get(key or "", _PRESETS[DEFAULT_DELAY])
        return self._delay

    async def set_delay(self, key: str) -> DelayPreset:
        preset = _PRESETS.get(key)
        if preset is None:
            raise ValueError(f"Неизвестный режим паузы: {key}")
        await self._storage.set_setting(KEY_WA_DELAY, preset.key)
        self._delay = preset
        return preset

    async def delay_range(self) -> tuple[float, float]:
        preset = await self.delay()
        return preset.min_s, preset.max_s
