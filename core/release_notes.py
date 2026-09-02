"""Описание выпуска, приведённое к тому, что можно показать в окне.

GitHub отдаёт тело релиза разметкой Markdown: заголовки, вложенные списки,
жирный текст и хвост ссылок на коммиты и пул-реквесты. Ничего из этого в окне
не нужно, и ради этого не нужен движок Markdown — здесь тело релиза сводится к
разделам из простых строк, а вся обвязка выбрасывается.

Чистый текст на входе, чистые данные на выходе: ни сети, ни виджетов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Более глубокая вложенность схлопывается: окно — не оглавление.
MAX_LEVEL = 1
#: Дальше описание выпуска перестают читать и начинают пролистывать.
MAX_ITEMS = 40

_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*$")
_BULLET = re.compile(r"^(?P<indent>[ \t]*)[-*+]\s+(?P<text>.+)$")
_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)]*)\)")
_BARE_URL = re.compile(r"https?://\S+")
#: Звёздочки и обратные кавычки. Подчёркивания трогаем только когда они
#: обрамляют слово: ``pix_fmt`` и ``check_updates.enabled`` — это имена.
_EMPHASIS = re.compile(r"(\*\*\*|\*\*|\*|`)")
_UNDERSCORE_EMPHASIS = re.compile(r"(?<!\w)(__?)(?=\S)(.+?)(?<=\S)\1(?!\w)")
_WHITESPACE = re.compile(r"\s+")
#: «by @someone in https://…/pull/123» — авторство оставляем, ссылку нет.
_TRAILING_REF = re.compile(r"\s+in\s+https?://\S+$", re.IGNORECASE)
#: Хвост «([a1b2c3](ссылка))» или «(#123)», которым инструменты выпуска
#: подписывают каждый пункт: адрес коммита читателю описания не нужен.
#: Обязательна именно ссылочная форма — иначе под правило попало бы любое
#: слово из шестнадцатеричных букв в скобках, вроде «(facade)».
_TRAILING_COMMIT = re.compile(r"\s*\(\s*\[[0-9a-f]{6,40}\]\([^)]*\)\s*\)\s*$", re.IGNORECASE)
_TRAILING_ISSUE = re.compile(r"\s*\(#\d+\)\s*$")
#: Строки, которые существуют только чтобы куда-то увести.
_LINK_ONLY_LINE = re.compile(r"^\**\s*(full changelog|changelog|see also)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NoteItem:
    """Одна строка описания; ``level`` — глубина вложенности."""

    text: str
    level: int = 0


@dataclass(frozen=True)
class NoteSection:
    """Заголовок и строки под ним. Для вступления ``title`` пустой."""

    title: str
    items: tuple[NoteItem, ...]

    def __bool__(self) -> bool:
        return bool(self.items)


def parse_release_notes(markdown: str, *, max_items: int = MAX_ITEMS) -> tuple[NoteSection, ...]:
    """Разбивает тело релиза на разделы для показа. Не бросает исключений."""
    sections: list[tuple[str, list[NoteItem]]] = []
    current: tuple[str, list[NoteItem]] = ("", [])
    sections.append(current)
    total = 0

    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        heading = _HEADING.match(line.strip())
        if heading:
            current = (clean_text(heading.group("title")), [])
            sections.append(current)
            continue

        if total >= max_items:
            break
        item = _as_item(line)
        if item is not None:
            current[1].append(item)
            total += 1

    return tuple(NoteSection(title, tuple(items)) for title, items in sections if items)


def clean_text(text: str) -> str:
    """Markdown до слов: без выделения, без кавычек кода, без голых ссылок."""
    trimmed = _TRAILING_COMMIT.sub("", text)
    trimmed = _TRAILING_ISSUE.sub("", trimmed)
    without_links = _LINK.sub(lambda m: m.group("text") or m.group("url"), trimmed)
    without_refs = _TRAILING_REF.sub("", without_links)
    plain = _UNDERSCORE_EMPHASIS.sub(lambda m: m.group(2), without_refs)
    plain = _EMPHASIS.sub("", plain)
    plain = _BARE_URL.sub("", plain)
    return _WHITESPACE.sub(" ", plain).strip(" -–—:;,")


def summarize(sections: tuple[NoteSection, ...], limit: int = 3) -> str:
    """Первые строки — для однострочного уведомления в строке состояния."""
    lines = [item.text for section in sections for item in section.items][:limit]
    return "; ".join(lines)


def _as_item(line: str) -> NoteItem | None:
    bullet = _BULLET.match(line)
    if bullet:
        indent = bullet.group("indent").replace("\t", "  ")
        level = min(len(indent) // 2, MAX_LEVEL)
        text = clean_text(bullet.group("text"))
    else:
        stripped = line.strip()
        if _LINK_ONLY_LINE.match(stripped):
            return None
        level = 0
        text = clean_text(stripped)
    # Остатки из одной пунктуации — одинокая решётка, линейка из дефисов,
    # шальные звёздочки — это оформление, а не содержание.
    return NoteItem(text=text, level=level) if _has_words(text) else None


def _has_words(text: str) -> bool:
    return any(char.isalnum() for char in text)
