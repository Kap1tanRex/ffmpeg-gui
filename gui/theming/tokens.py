"""Токены дизайна: единственный источник цветов, радиусов и шрифтов.

Тема — это JSON с токенами (``themes/dark.json``, ``themes/light.json``),
из которого собирается тема CustomTkinter. Шаблон темы фиксирован: из файла
подставляются только значения токенов, поэтому пользовательская тема не может
подменить структуру оформления — только палитру.

CustomTkinter принимает цвет либо строкой, либо парой ``(светлая, тёмная)`` и
сам выбирает нужную половину по текущему режиму оформления. Поэтому светлая и
тёмная темы загружаются вместе, а :func:`pair` отдаёт готовую пару — то же
значение работает и в «Светлой», и в «Тёмной», и в режиме «Системная».
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

THEMES_DIR = Path(__file__).parent / "themes"

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SIZE_RE = re.compile(r"^\d{1,3}$")
_FONT_RE = re.compile(r"^[\w\s,\-]+$")

#: Значения, которые используются, если файл темы отсутствует или повреждён.
_FALLBACK: dict[str, str] = {
    "bg.window": "#14161A",
    "bg.surface": "#1C1F25",
    "bg.elevated": "#242830",
    "bg.hover": "#2A2F38",
    "bg.selected": "#2F3742",
    "fg.primary": "#E8EAED",
    "fg.secondary": "#9AA0A6",
    "fg.disabled": "#5F6368",
    "fg.inverted": "#14161A",
    "accent": "#4C8DFF",
    "accent.hover": "#679DFF",
    "accent.pressed": "#3A78E0",
    "accent.fg": "#FFFFFF",
    "state.ok": "#3FB950",
    "state.warn": "#D29922",
    "state.error": "#F85149",
    "state.info": "#58A6FF",
    "border": "#2C313A",
    "border.strong": "#3A414D",
    "radius.sm": "7",
    "radius.md": "10",
    "radius.lg": "14",
    "font.family": "Segoe UI",
    "font.family.mono": "Cascadia Mono",
    "font.size": "13",
    "font.size.small": "12",
    "font.size.large": "15",
    "font.size.hero": "22",
}


def _validate(tokens: dict, source: str) -> bool:
    """Отвергает всё, что не является цветом, размером или именем шрифта."""
    for key, value in tokens.items():
        text = str(value)
        if key.startswith(("bg.", "fg.", "accent", "state.", "border")):
            if not _COLOR_RE.match(text):
                log.warning("тема %s: недопустимый цвет %s = %r", source, key, text)
                return False
        elif key.startswith(("radius.", "font.size")):
            if not _SIZE_RE.match(text):
                log.warning("тема %s: недопустимый размер %s = %r", source, key, text)
                return False
        elif key.startswith("font.family") and not _FONT_RE.match(text):
            log.warning("тема %s: недопустимый шрифт %s = %r", source, key, text)
            return False
    return True


def load_tokens(path: Path) -> dict[str, str]:
    """Читает файл темы; при любой ошибке возвращает значения по умолчанию."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("не удалось прочитать тему %s: %s", path.name, exc)
        return dict(_FALLBACK)
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict) or not _validate(tokens, path.name):
        return dict(_FALLBACK)
    merged = dict(_FALLBACK)
    merged.update({str(k): str(v) for k, v in tokens.items()})
    return merged


class Palette:
    """Пара палитр (светлая/тёмная) и доступ к токенам в формате CustomTkinter."""

    def __init__(self, light: dict[str, str], dark: dict[str, str]) -> None:
        self.light = light
        self.dark = dark

    def pair(self, token: str) -> tuple[str, str]:
        """Цвет как пара ``(светлая тема, тёмная тема)``."""
        fallback = _FALLBACK.get(token, "#808080")
        return (self.light.get(token, fallback), self.dark.get(token, fallback))

    def size(self, token: str) -> int:
        raw = self.dark.get(token) or _FALLBACK.get(token, "0")
        try:
            return int(raw)
        except ValueError:
            return 0

    def family(self, token: str = "font.family") -> str:
        return self.dark.get(token) or _FALLBACK.get(token, "Segoe UI")

    def set_accent(self, accent: str) -> None:
        """Акцентный цвет настраивается поверх любой темы (как в ZapretGUI)."""
        if not _COLOR_RE.match(accent):
            return
        for palette in (self.light, self.dark):
            palette["accent"] = accent
            palette["accent.hover"] = _shift(accent, 1.12)
            palette["accent.pressed"] = _shift(accent, 0.88)


def _shift(color: str, factor: float) -> str:
    """Осветляет (factor > 1) или затемняет (factor < 1) цвет #RRGGBB."""
    text = color.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        channels = [int(text[i : i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return color
    return "#" + "".join(f"{max(0, min(255, int(c * factor))):02X}" for c in channels)


def _load_palette(user_dir: Path | None = None) -> Palette:
    """Встроенные темы, поверх которых — пользовательские из каталога настроек."""
    light = load_tokens(THEMES_DIR / "light.json")
    dark = load_tokens(THEMES_DIR / "dark.json")
    if user_dir is not None:
        for name, target in (("light.json", light), ("dark.json", dark)):
            custom = user_dir / name
            if custom.is_file():
                target.update(load_tokens(custom))
    return Palette(light, dark)


#: Активная палитра приложения.
PALETTE = _load_palette()


def reload_palette(user_dir: Path | None = None) -> Palette:
    """Перечитывает темы, учитывая пользовательские файлы из каталога настроек."""
    global PALETTE
    PALETTE = _load_palette(user_dir)
    return PALETTE


# ── короткие обёртки, которыми пользуются виджеты ────────────────────────────
def pair(token: str) -> tuple[str, str]:
    return PALETTE.pair(token)


def radius(name: str = "md") -> int:
    return PALETTE.size(f"radius.{name}")
