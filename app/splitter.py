"""Нарезка длинного текста на части для MAX (лимит 4000 символов)."""

from __future__ import annotations

import re

MAX_TEXT_LIMIT = 4000

_BLANK_LINES = re.compile(r"\n[ \t]*\n")


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _pack(pieces: list[str], sep: str, limit: int) -> list[str]:
    """Жадно склеивает куски через sep, не превышая limit."""
    parts: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}{sep}{piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = piece
    if current:
        parts.append(current)
    return parts


def split_text(text: str, limit: int = MAX_TEXT_LIMIT) -> list[str]:
    """Режет текст на части ≤ limit по пустым строкам (границам абзацев).

    Абзац длиннее limit режется по переводам строк, строка длиннее limit — жёстко.
    Строки не теряются: склейка частей через пустую строку восстанавливает текст
    с точностью до пробелов в разделителях.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    for paragraph in _BLANK_LINES.split(text):
        paragraph = paragraph.strip("\n")
        if not paragraph.strip():
            continue
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        lines: list[str] = []
        for line in paragraph.split("\n"):
            lines.extend(_hard_split(line, limit) if len(line) > limit else [line])
        # Куски одного длинного абзаца склеиваем через "\n", чтобы не плодить пустые строки.
        pieces.extend(_pack(lines, "\n", limit))
    return _pack(pieces, "\n\n", limit)
