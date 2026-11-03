from __future__ import annotations

import pytest

from app.splitter import MAX_TEXT_LIMIT, split_text


def lines_of(parts: list[str]) -> list[str]:
    return [line for part in parts for line in part.split("\n") if line.strip()]


def test_short_text_single_part() -> None:
    assert split_text("Привет 🔴\n\nМир") == ["Привет 🔴\n\nМир"]


def test_empty() -> None:
    assert split_text("   \n\n ") == []


def test_exact_limit_not_split() -> None:
    text = "x" * MAX_TEXT_LIMIT
    assert split_text(text) == [text]


def test_splits_on_blank_lines() -> None:
    paragraphs = [f"🔴Абзац {i}\n" + "строка " * 100 for i in range(12)]
    text = "\n\n".join(paragraphs)
    parts = split_text(text)
    assert len(parts) > 1
    assert all(len(p) <= MAX_TEXT_LIMIT for p in parts)
    # границы частей проходят только по абзацам: каждый абзац целиком в одной части
    for paragraph in paragraphs:
        assert sum(paragraph.strip() in p for p in parts) == 1
    assert "\n\n".join(parts) == text.strip()


def test_no_lines_lost_with_long_paragraph() -> None:
    # Абзац без пустых строк длиннее лимита → режется по строкам.
    long_paragraph = "\n".join(f"Строка {i}: подсолнечник 400 тн, 3300 руб/тн" for i in range(200))
    text = "Аванс 50%\n\n" + long_paragraph + "\n\nКонец"
    parts = split_text(text)
    assert all(len(p) <= MAX_TEXT_LIMIT for p in parts)
    assert lines_of(parts) == [line for line in text.split("\n") if line.strip()]


def test_single_huge_line_hard_split() -> None:
    text = "я" * 9000
    parts = split_text(text)
    assert [len(p) for p in parts] == [4000, 4000, 1000]
    assert "".join(parts) == text


@pytest.mark.parametrize("limit", [50, 100, 333])
def test_custom_limit_preserves_content(limit: int) -> None:
    text = "\n\n".join(f"Абзац {i}\nвторая строка {i}" for i in range(40))
    parts = split_text(text, limit)
    assert all(len(p) <= limit for p in parts)
    assert lines_of(parts) == [line for line in text.split("\n") if line.strip()]


def test_windows_blank_lines_with_spaces() -> None:
    text = ("a" * 3000) + "\n  \n" + ("b" * 3000)
    assert split_text(text) == ["a" * 3000, "b" * 3000]
