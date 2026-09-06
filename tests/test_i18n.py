"""Перевод интерфейса: словарь, подстановка и полнота."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ffmpeg_gui.core import i18n
from ffmpeg_gui.core.help_registry import crf_quality, registry
from ffmpeg_gui.core.naming import name_values, render_name

EN = Path(i18n.__file__).resolve().parent.parent / "resources" / "translations" / "en.json"


@pytest.fixture
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("ru")


def test_russian_is_the_source_language():
    i18n.set_language("ru")
    assert i18n.language() == "ru"
    assert i18n.t("Сжатие") == "Сжатие"


def test_english_dictionary_loads(english):
    assert i18n.language() == "en"
    assert i18n.t("Сжатие") == "Compress"


def test_unknown_string_falls_back_to_source(english):
    """Пропущенный перевод показывает русский текст, а не пустоту."""
    assert i18n.t("такой строки в словаре нет") == "такой строки в словаре нет"


def test_unknown_language_falls_back_to_russian():
    i18n.set_language("xx")
    assert i18n.language() == "ru"
    assert i18n.t("Сжатие") == "Сжатие"


def test_template_is_translated_before_substitution(english):
    """Иначе в словаре пришлось бы держать строку на каждое значение."""
    assert i18n.tf("Очередь: {count}", count=7) == "Queue: 7"


def test_broken_template_does_not_crash(english):
    """Ошибка в подстановке показывает шаблон, а не роняет окно."""
    assert i18n.tf("Очередь: {count}", wrong=1) == "Queue: {count}"


# --- содержание словаря -----------------------------------------------------
def test_dictionary_is_valid_json_of_strings():
    data = json.loads(EN.read_text(encoding="utf-8"))
    assert data, "словарь пуст"
    bad = [k for k, v in data.items() if not isinstance(v, str) or not v]
    assert not bad, f"пустые переводы: {bad[:5]}"


def test_placeholders_match_between_languages():
    """Подстановки должны совпадать, иначе перевод уронит подстановку."""
    import re

    data = json.loads(EN.read_text(encoding="utf-8"))
    pattern = re.compile(r"\{(\w+)\}")
    cyrillic = re.compile(r"[А-Яа-яЁё]")

    mismatched = []
    for source, target in data.items():
        names = pattern.findall(source)
        # Строка, перечисляющая токены шаблона имени файла, — не формат, а
        # документация: сами имена токенов тоже переводятся ({имя} -> {name}).
        if any(cyrillic.search(name) for name in names):
            continue
        if set(names) != set(pattern.findall(target)):
            mismatched.append(source)
    assert not mismatched, f"разные подстановки: {mismatched[:3]}"


def test_no_translation_left_in_russian():
    """Перевод, совпадающий с исходником, — почти наверняка забытая строка."""
    data = json.loads(EN.read_text(encoding="utf-8"))
    same = [k for k, v in data.items() if k == v]
    assert not same, f"не переведено: {same[:5]}"


# --- перевод на границе показа ----------------------------------------------
def test_help_entries_are_translated(english):
    sections = registry.get("crf").sections()
    assert sections[0][1].startswith("Constant quality")


def test_quality_scale_is_translated(english):
    assert crf_quality(51)[0] == "garbage"
    assert crf_quality(23)[0].startswith("good")


def test_filename_tokens_work_in_both_languages(english):
    """Шаблон, написанный на любом языке, должен работать."""
    values = name_values("Holiday", "_compressed", height=1080, codec="hevc")
    assert render_name("{name}_{height}p_{codec}", values) == "Holiday_1080p_hevc"
    assert render_name("{имя}_{высота}p_{кодек}", values) == "Holiday_1080p_hevc"


def test_available_languages_lists_both():
    languages = i18n.available_languages()
    assert "ru" in languages and "en" in languages
    assert languages["en"] == "English"
