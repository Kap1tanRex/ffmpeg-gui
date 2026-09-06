"""Шкала качества CRF, подсказки и шаблон имени файла."""

from __future__ import annotations

import pytest

from ffmpeg_gui.core.help_registry import EFFECT, TITLE, crf_quality, crf_range, registry
from ffmpeg_gui.core.naming import DEFAULT_TEMPLATE, name_values, render_name

#: Насколько плох уровень: чем больше, тем хуже.
_BADNESS = {"ok": 0, "info": 1, "warn": 2, "error": 3}


def test_zero_crf_is_lossless():
    assert crf_quality(0)[0] == "без потерь"


def test_high_crf_is_the_bad_end():
    """CRF — «насколько разрешено ухудшить»: шакальность на большом числе."""
    assert crf_quality(51)[0] == "шакальность"
    assert crf_quality(51)[1] == "error"


def test_quality_never_improves_as_crf_grows():
    levels = [_BADNESS[crf_quality(value)[1]] for value in (20, 25, 30, 38, 50)]
    assert levels == sorted(levels)


def test_typical_values_read_as_good():
    assert crf_quality(18)[1] == "ok"
    assert crf_quality(23)[1] == "ok"


def test_scale_depends_on_codec():
    assert crf_range("av1") == (0.0, 63.0)
    assert crf_range("h264") == (0.0, 51.0)
    # Одно и то же качество на разных шкалах выражается разными числами.
    assert crf_quality(30, "av1") == crf_quality(24, "h264")


def test_no_value_gives_no_label():
    assert crf_quality(None)[0] == ""


@pytest.mark.parametrize(
    "key",
    [
        "crf", "quality_mode", "target_size", "two_pass", "preset", "bitrate",
        "normalize", "resolution", "fps", "hwaccel", "stream_copy", "codec",
        "auto_hardware", "trim_accurate", "output_template", "filters", "crop",
        "rotate", "deinterlace", "denoise", "watermark", "watch_folder", "ui_scale",
    ],
)
def test_help_entry_exists_and_says_what_happens_to_the_file(key):
    entry = registry.get(key)
    assert entry is not None, f"нет подсказки для {key}"
    assert entry.brief, f"у {key} пустой заголовок"
    assert entry.effect, f"у {key} не сказано, что будет с файлом"


def test_help_sections_start_with_title_and_include_effect():
    sections = registry.get("crf").sections()
    assert sections[0][0] == TITLE
    assert any(role == EFFECT for role, _text in sections)


def test_help_is_short_enough_to_read_on_the_fly():
    too_long = [key for key in registry._entries if len(registry.get(key).text()) > 700]
    assert not too_long


def test_encoder_hint_names_both_sides():
    hint = registry.encoder_hint("h264_nvenc")
    assert "быстрее" in hint and "грубее" in hint


# --- шаблон имени файла -----------------------------------------------------
def _values():
    return name_values(
        "Отпуск 2026", "_compressed", codec="hevc", width=1920, height=1080,
        quality="crf23", profile="Мессенджер",
    )


def test_default_template_keeps_previous_behaviour():
    assert render_name(DEFAULT_TEMPLATE, _values()) == "Отпуск 2026_compressed"


def test_template_substitutes_tokens():
    assert render_name("{имя}_{высота}p_{кодек}", _values()) == "Отпуск 2026_1080p_hevc"


def test_unknown_token_becomes_empty_instead_of_failing():
    assert render_name("{имя}_{неизвестно}", _values()) == "Отпуск 2026"


def test_forbidden_characters_are_removed():
    assert ":" not in render_name('{имя}:<плохо>?', _values())


def test_empty_template_falls_back_to_default():
    assert render_name("", _values()) == "Отпуск 2026_compressed"


def test_name_never_ends_up_empty():
    assert render_name("{неизвестно}", _values()) == "output"
