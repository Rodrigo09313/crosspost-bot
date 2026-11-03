"""Rich-сообщения Telegram (Bot API 10.1+): таблицы, заголовки, сворачиваемые блоки.

Только разметка, без строк интерфейса — тексты собирает `app/texts.py`.
"""

from __future__ import annotations

from aiogram.types import (
    InputRichBlockDetails,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockTable,
    InputRichBlockUnion,
    InputRichMessage,
    RichBlockTableCell,
    RichTextBold,
)


def message(blocks: list[InputRichBlockUnion]) -> InputRichMessage:
    return InputRichMessage(blocks=blocks)


def heading(text: str, size: int = 3) -> InputRichBlockSectionHeading:
    return InputRichBlockSectionHeading(text=text, size=size)


def paragraph(text: str) -> InputRichBlockParagraph:
    return InputRichBlockParagraph(text=text)


def details(summary: str, lines: list[str]) -> InputRichBlockDetails:
    """Свёрнутый блок: заголовок виден, строки — по нажатию."""
    return InputRichBlockDetails(summary=summary, blocks=[paragraph(line) for line in lines])


def _cell(text: str, *, header: bool = False) -> RichBlockTableCell:
    return RichBlockTableCell(
        text=RichTextBold(text=text) if header else text,
        is_header=True if header else None,
        align="left",
        valign="middle",
    )


def table(head: list[str], rows: list[list[str]]) -> InputRichBlockTable:
    return InputRichBlockTable(
        cells=[[_cell(h, header=True) for h in head]] + [[_cell(c) for c in row] for row in rows],
        is_bordered=True,
        is_striped=True,
        is_compact=True,
    )
