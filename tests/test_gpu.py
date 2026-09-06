"""Определение видеокарты и версии драйвера."""

from __future__ import annotations

import pytest

from ffmpeg_gui.services.gpu_detector import (
    GpuInfo,
    hardware_suffixes_for_vendor,
    nvidia_release,
    status_caption,
    summarize,
)


# --- перевод нумерации драйвера NVIDIA --------------------------------------
@pytest.mark.parametrize(
    ("windows_version", "expected"),
    [
        ("32.0.15.6094", "560.94"),
        ("31.0.15.2824", "528.24"),
        ("32.0.15.6636", "566.36"),
        ("32.0.16.1656", "616.56"),
    ],
)
def test_nvidia_release_matches_vendor_numbering(windows_version, expected):
    """Windows называет драйвер по-своему, а FFmpeg и NVIDIA — иначе."""
    assert nvidia_release(windows_version) == expected


@pytest.mark.parametrize("garbage", ["", "31", "не.версия", "1.2.3.abcd"])
def test_nvidia_release_survives_garbage(garbage):
    assert nvidia_release(garbage) == ""


# --- подписи ----------------------------------------------------------------
def test_caption_names_vendor_and_driver():
    gpus = [GpuInfo("NVIDIA GeForce RTX 3080", "NVIDIA", "566.36")]
    assert status_caption(gpus) == "NVIDIA 566.36"


def test_caption_without_driver_is_just_the_vendor():
    assert status_caption([GpuInfo("GeForce", "NVIDIA")]) == "NVIDIA"


def test_caption_skips_unrecognised_adapters():
    """Виртуальные адаптеры не должны вытеснять настоящую видеокарту."""
    gpus = [
        GpuInfo("Microsoft Remote Display Adapter", "UNKNOWN"),
        GpuInfo("NVIDIA GeForce RTX 3080", "NVIDIA", "566.36"),
    ]
    assert status_caption(gpus) == "NVIDIA 566.36"


def test_caption_without_any_gpu():
    assert status_caption([]) == "GPU"


def test_summary_includes_driver():
    gpus = [GpuInfo("NVIDIA GeForce RTX 3080", "NVIDIA", "566.36")]
    assert summarize(gpus) == "NVIDIA GeForce RTX 3080 (NVIDIA), драйвер 566.36"


def test_summary_without_driver_says_nothing_extra():
    assert summarize([GpuInfo("GeForce", "NVIDIA")]) == "GeForce (NVIDIA)"


def test_summary_without_gpu():
    assert summarize([]) == "Видеокарты не обнаружены"


# --- соответствие производителя и энкодеров ---------------------------------
@pytest.mark.parametrize(
    ("vendor", "suffix"),
    [("NVIDIA", "nvenc"), ("AMD", "amf"), ("INTEL", "qsv")],
)
def test_vendor_maps_to_encoder_suffix(vendor, suffix):
    assert suffix in hardware_suffixes_for_vendor(vendor)


def test_unknown_vendor_has_no_suffixes():
    assert hardware_suffixes_for_vendor("UNKNOWN") == ()
