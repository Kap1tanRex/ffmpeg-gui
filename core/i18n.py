"""Перевод интерфейса.

Ключ перевода — сама русская строка, а не выдуманный идентификатор вроде
``settings.gpu.title``. Так у каждой строки всегда есть осмысленное значение
по умолчанию: пропущенный перевод показывает русский текст, а не пустоту и не
``settings.gpu.title`` посреди окна.

Переводы лежат в ``resources/translations/en.json`` словарём
``{"русская строка": "English string"}``. Русский — исходный язык, файла для
него не нужно.

Переводить принято на границе показа: подсказки, ошибки и подписи шкалы
качества проходят через :func:`t` в тот момент, когда их запрашивает
интерфейс, а не в месте, где написан текст. Иначе пришлось бы обернуть
несколько сотен литералов и следить, чтобы ни один не потерялся.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Исходный язык: строки в коде написаны на нём, файла перевода у него нет.
SOURCE_LANGUAGE = "ru"

#: Язык -> человеческое название для списка в настройках.
LANGUAGES: dict[str, str] = {"ru": "Русский", "en": "English"}

_TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "resources" / "translations"

_language = SOURCE_LANGUAGE
_strings: dict[str, str] = {}


def available_languages() -> dict[str, str]:
    """Языки, для которых есть перевод (плюс исходный)."""
    found = {SOURCE_LANGUAGE: LANGUAGES[SOURCE_LANGUAGE]}
    if _TRANSLATIONS_DIR.is_dir():
        for path in sorted(_TRANSLATIONS_DIR.glob("*.json")):
            if path.stem != SOURCE_LANGUAGE:
                found[path.stem] = LANGUAGES.get(path.stem, path.stem)
    return found


def language() -> str:
    return _language


def set_language(code: str) -> None:
    """Переключает язык и загружает словарь.

    Вызывается до создания окна: подписи виджетов читаются в момент их
    постройки, поэтому смена языка на лету потребовала бы пересборки окна.
    """
    global _language, _strings
    code = (code or SOURCE_LANGUAGE).strip().lower()
    if code == SOURCE_LANGUAGE:
        _language, _strings = SOURCE_LANGUAGE, {}
        return

    path = _TRANSLATIONS_DIR / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Перевод %s не загружен: %s", code, exc)
        _language, _strings = SOURCE_LANGUAGE, {}
        return

    _strings = {k: v for k, v in data.items() if isinstance(v, str) and v}
    _language = code
    log.info("Язык интерфейса: %s (строк в словаре: %d)", code, len(_strings))


def t(text: str) -> str:
    """Переводит строку; без перевода возвращает её как есть."""
    if not text or not _strings:
        return text
    return _strings.get(text, text)


def tf(template: str, **values) -> str:
    """Переводит шаблон и подставляет значения.

    Порядок важен: подставлять надо в переведённый шаблон, иначе в словаре
    пришлось бы держать по строке на каждое подставленное число.
    """
    translated = t(template)
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError):
        # Опечатка в переводе не должна ронять интерфейс. Возвращаем текст
        # без подстановки: повторять format() с теми же значениями
        # бессмысленно — он упадёт ровно так же.
        log.warning("Не удалось подставить значения в перевод: %r", template)
        return translated


def missing(texts: list[str]) -> list[str]:
    """Строки без перевода — для проверки полноты словаря."""
    if _language == SOURCE_LANGUAGE:
        return []
    return [text for text in texts if text and text not in _strings]
