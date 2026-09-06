"""Имя выходного файла по шаблону пользователя.

При пакетной обработке имя — единственное, по чему потом можно понять, что
за файл и с какими параметрами он сделан. Шаблон задаётся в настройках и
подставляется здесь; модуль ничего не знает ни о GUI, ни о файловой системе.
"""

from __future__ import annotations

import re
from datetime import datetime

from .i18n import t

#: Что подставляется по умолчанию: имя исходника и суффикс операции —
#: ровно то поведение, что было до появления шаблонов.
DEFAULT_TEMPLATE = "{имя}{суффикс}"

#: Токен -> пояснение. Показывается пользователю рядом с полем шаблона.
TOKENS: dict[str, str] = {
    "имя": "имя исходного файла без расширения",
    "суффикс": "_compressed, _converted или _trimmed — по операции",
    "кодек": "выбранный кодек: h264, hevc, av1",
    "ширина": "ширина кадра результата",
    "высота": "высота кадра результата",
    "качество": "значение CRF или заданный размер",
    "профиль": "имя выбранного профиля",
    "дата": "дата запуска, ГГГГ-ММ-ДД",
    "время": "время запуска, ЧЧ-ММ",
}

_TOKEN = re.compile(r"\{([^{}]*)\}")
#: Символы, которых не бывает в именах файлов Windows.
_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SPACES = re.compile(r"\s{2,}")


def render_name(template: str, values: dict[str, str]) -> str:
    """Подставляет ``{токены}`` и приводит результат к пригодному имени файла.

    Неизвестный или пустой токен подставляется пустой строкой: шаблон — это
    настройка пользователя, и опечатка в ней не должна ронять задание.
    """

    def substitute(match: re.Match[str]) -> str:
        return str(values.get(match.group(1).strip(), "") or "")

    name = _TOKEN.sub(substitute, template or DEFAULT_TEMPLATE)
    name = _FORBIDDEN.sub("", name)
    # Пустые токены оставляют после себя двойные разделители: «файл__2024».
    name = _SPACES.sub(" ", name).strip(" .-_")
    while "__" in name:
        name = name.replace("__", "_")
    return name or "output"


def name_values(
    stem: str,
    suffix: str = "",
    *,
    codec: str = "",
    width: int | None = None,
    height: int | None = None,
    quality: str = "",
    profile: str = "",
    moment: datetime | None = None,
) -> dict[str, str]:
    """Собирает значения токенов; пустые остаются пустыми строками."""
    moment = moment or datetime.now()
    values = {
        "имя": stem,
        "суффикс": suffix,
        "кодек": codec if codec and codec != "auto" else "",
        "ширина": str(width) if width else "",
        "высота": str(height) if height else "",
        "качество": quality,
        "профиль": profile,
        "дата": moment.strftime("%Y-%m-%d"),
        "время": moment.strftime("%H-%M"),
    }
    # Шаблон пишет пользователь, и на английском он напишет {name}, а не
    # {имя}. Оба набора имён понимаются одновременно, поэтому шаблон,
    # сохранённый на одном языке, продолжает работать на другом.
    values.update({t(name): value for name, value in values.items()})
    return values
