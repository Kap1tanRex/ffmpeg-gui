"""Сборка команды: целевой размер, два прохода, фильтры, склейка."""

from __future__ import annotations

import os

import pytest

from ffmpeg_gui.core.command_builder import CommandBuilder, CommandBuildError
from ffmpeg_gui.core.filters import DENOISE, ROTATIONS, FilterOptions
from ffmpeg_gui.core.models import (
    Job,
    MediaFile,
    MediaStream,
    StreamType,
    parse_bitrate,
)
from pathlib import Path

DURATION = 60.0


@pytest.fixture
def builder():
    return CommandBuilder()


@pytest.fixture
def job():
    source = MediaFile(
        path=Path(r"C:\in.mp4"),
        duration=DURATION,
        size=200 * 1024 * 1024,
        streams=[MediaStream(index=0, type=StreamType.AUDIO, bit_rate=192_000)],
    )
    result = Job(input_files=[r"C:\in.mp4"], output_file=r"C:\out.mp4", source=source)
    result.container = "mp4"
    result.video_encoder = "libx264"
    result.audio_encoder = "aac"
    result.audio.bitrate = "160k"
    return result


# --- разбор битрейта --------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [("160k", 160_000), ("2M", 2_000_000), ("4500000", 4_500_000)],
)
def test_parse_bitrate(text, expected):
    assert parse_bitrate(text) == expected


def test_parse_bitrate_rejects_garbage():
    assert parse_bitrate("мусор") is None
    assert parse_bitrate(None) is None


# --- целевой размер ---------------------------------------------------------
def test_target_bitrate_fits_requested_size(builder, job):
    job.video.quality_mode = "size"
    job.video.target_size_mb = 10.0
    bitrate = builder.target_bitrate(job)
    predicted = (bitrate + 160_000) * DURATION / 8
    assert predicted <= 10 * 1024 * 1024
    assert predicted > 9 * 1024 * 1024


def test_audio_budget_is_subtracted(builder, job):
    job.video.quality_mode = "size"
    job.video.target_size_mb = 10.0
    with_sound = builder.target_bitrate(job)
    job.audio.mode = "none"
    assert builder.target_bitrate(job) > with_sound


def test_trim_shortens_the_material(builder, job):
    job.video.quality_mode = "size"
    job.video.target_size_mb = 10.0
    full = builder.target_bitrate(job)
    job.trim.enabled = True
    job.trim.start = 0.0
    job.trim.duration = DURATION / 2
    assert builder.target_bitrate(job) > full * 1.8


def test_size_mode_without_duration_reports_clearly(builder):
    orphan = Job(input_files=[r"C:\in.mp4"], output_file=r"C:\out.mp4")
    orphan.video_encoder = "libx264"
    orphan.video.quality_mode = "size"
    orphan.video.target_size_mb = 10.0
    with pytest.raises(CommandBuildError, match="длительность"):
        builder.build(orphan)


# --- два прохода ------------------------------------------------------------
def test_first_pass_writes_nowhere_and_skips_sound(builder, job):
    command = builder.build(job, pass_number=1, passlog=r"C:\tmp\out.pass")
    assert command[-1] == os.devnull
    assert command[command.index("-pass") + 1] == "1"
    assert "-an" in command and "-sn" in command
    assert "-c:a" not in command


def test_first_pass_keeps_explicit_stream_map(builder, job):
    """При явном выборе потоков -an даст конфликт, поэтому его не добавляем."""
    job.stream_map = ["0:v:0", "0:a:1"]
    assert "-an" not in builder.build(job, pass_number=1, passlog="x")


def test_second_pass_writes_the_file(builder, job):
    command = builder.build(job, output_override=r"C:\tmp\out.tmp", pass_number=2, passlog="x")
    assert command[command.index("-pass") + 1] == "2"
    assert command[-1] == r"C:\tmp\out.tmp"
    assert "-c:a" in command


# --- нормализация громкости -------------------------------------------------
def test_loudnorm_added_after_manual_volume(builder, job):
    job.audio.normalize = True
    job.audio.volume = 1.5
    chain = builder.build(job)[builder.build(job).index("-af") + 1]
    assert chain.index("volume=") < chain.index("loudnorm")


# --- фильтры ----------------------------------------------------------------
def test_filter_order_rotate_crop_denoise():
    options = FilterOptions(
        rotate=ROTATIONS["На 90° вправо"],
        crop_width=640, crop_height=480, crop_x=10, crop_y=20,
        deinterlace=True, denoise=DENOISE["Среднее"],
    )
    chain = options.build()
    assert chain[0] == "transpose=1"
    assert chain[1] == "crop=640:480:10:20"
    assert "yadif" in chain
    assert chain[-1].startswith("hqdn3d")


def test_empty_filter_panel_adds_nothing():
    assert not FilterOptions().active


def test_crop_needs_both_sides():
    assert FilterOptions(crop_width=640).crop_filter() == ""


def test_plain_filters_use_vf(builder, job):
    job.filters = FilterOptions(rotate="transpose=1").build()
    command = builder.build(job)
    assert "-vf" in command
    assert "-filter_complex" not in command


def test_watermark_switches_to_filter_complex(builder, job):
    job.filters = FilterOptions(crop_width=400, crop_height=300).build()
    job.overlay_path = r"C:\mark.png"
    job.overlay_position = "tr"
    job.overlay_opacity = 0.5
    command = builder.build(job)
    graph = command[command.index("-filter_complex") + 1]
    assert "-vf" not in command
    assert command.count("-i") == 2
    assert "crop=400:300" in graph
    assert "colorchannelmixer=aa=0.50" in graph
    assert "W-w-16:16" in graph
    assert command[command.index("-map") + 1] == "[v]"
    assert "0:a?" in command


def test_opaque_watermark_skips_the_extra_filter(builder, job):
    job.overlay_path = r"C:\mark.png"
    command = builder.build(job)
    assert "colorchannelmixer" not in command[command.index("-filter_complex") + 1]


# --- GIF --------------------------------------------------------------------
def test_gif_builds_its_own_palette(builder, job):
    job.container = "gif"
    job.video_encoder = "gif"
    command = builder.build(job)
    graph = command[command.index("-filter_complex") + 1]
    assert "palettegen" in graph and "paletteuse" in graph
    assert "-an" in command


# --- склейка ----------------------------------------------------------------
def test_concat_without_reencoding_uses_the_list(builder, job):
    job.concat = True
    job.concat_list = r"C:\tmp\list.txt"
    command = builder.build(job)
    assert command[command.index("-f") + 1] == "concat"
    assert "copy" in command
    assert "-c:v" not in command


def test_concat_with_reencoding_normalises_the_pieces(builder, job):
    """Фильтр concat отказывается работать при разном размере кадра."""
    job.concat = True
    job.input_files = [r"C:\a.mp4", r"C:\b.mp4"]
    job.source.streams.append(
        MediaStream(index=1, type=StreamType.VIDEO, width=640, height=480)
    )
    command = builder.build(job)
    graph = command[command.index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1" in graph
    assert graph.count("scale=640:480") == 2
    assert graph.count("aresample=48000") == 2
