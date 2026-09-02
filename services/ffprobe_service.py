"""Разбор JSON-вывода FFprobe в модель :class:`MediaFile` (раздел 7).

Также используется для проверки результата перед переименованием
(раздел 54): после кодирования временный файл прогоняется через FFprobe
и только при успешном разборе переименовывается в целевой.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core.models import MediaFile, MediaStream, StreamType
from .ffmpeg_service import FFmpegService, creation_flags

log = logging.getLogger(__name__)

_STREAM_TYPE_MAP = {
    "video": StreamType.VIDEO,
    "audio": StreamType.AUDIO,
    "subtitle": StreamType.SUBTITLE,
    "data": StreamType.DATA,
    "attachment": StreamType.ATTACHMENT,
}


@dataclass
class VerificationResult:
    ok: bool
    message: str = ""
    media: MediaFile | None = None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_probe_json(data: dict, path: Path) -> MediaFile:
    """Раздел 7: JSON ffprobe -> MediaFile. Не бросает исключений на мусоре."""
    fmt = data.get("format", {}) or {}
    streams_data = data.get("streams", []) or []

    streams: list[MediaStream] = []
    for raw in streams_data:
        stream_type = _STREAM_TYPE_MAP.get(raw.get("codec_type", ""), StreamType.UNKNOWN)
        stream = MediaStream(
            index=_to_int(raw.get("index")) or 0,
            type=stream_type,
            codec_name=raw.get("codec_name", "") or "",
            codec_long_name=raw.get("codec_long_name", "") or "",
            bit_rate=_to_int(raw.get("bit_rate")),
            duration=_to_float(raw.get("duration")),
            disposition={k: int(v) for k, v in (raw.get("disposition") or {}).items()},
            metadata={str(k): str(v) for k, v in (raw.get("tags") or {}).items()},
            profile=raw.get("profile"),
            level=_to_int(raw.get("level")),
            width=_to_int(raw.get("width")),
            height=_to_int(raw.get("height")),
            pix_fmt=raw.get("pix_fmt"),
            color_space=raw.get("color_space"),
            color_transfer=raw.get("color_transfer"),
            color_primaries=raw.get("color_primaries"),
            color_range=raw.get("color_range"),
            r_frame_rate=raw.get("r_frame_rate"),
            avg_frame_rate=raw.get("avg_frame_rate"),
            sample_rate=_to_int(raw.get("sample_rate")),
            channels=_to_int(raw.get("channels")),
            channel_layout=raw.get("channel_layout"),
            sample_fmt=raw.get("sample_fmt"),
        )
        streams.append(stream)

    media = MediaFile(
        path=path,
        format_name=fmt.get("format_name", "") or "",
        format_long_name=fmt.get("format_long_name", "") or "",
        duration=_to_float(fmt.get("duration")) or 0.0,
        size=_to_int(fmt.get("size")) or (path.stat().st_size if path.exists() else 0),
        bitrate=_to_int(fmt.get("bit_rate")),
        start_time=_to_float(fmt.get("start_time")),
        probe_score=_to_int(fmt.get("probe_score")),
        streams=streams,
        metadata={str(k): str(v) for k, v in (fmt.get("tags") or {}).items()},
        raw=data,
    )
    return media


class FFprobeService:
    """Запуск ffprobe и разбор результата."""

    def __init__(self, ffmpeg_service: FFmpegService) -> None:
        self.ffmpeg = ffmpeg_service

    def probe(self, path: Path) -> MediaFile:
        path = Path(path)
        binary = self.ffmpeg.binaries.ffprobe
        size = path.stat().st_size if path.exists() else 0
        if not binary:
            return MediaFile(path=path, size=size, probe_error="FFprobe не найден")

        try:
            result = subprocess.run(
                [
                    str(binary),
                    "-v", "error",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                creationflags=creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return MediaFile(path=path, size=size, probe_error=str(exc))

        if result.returncode != 0:
            message = (result.stderr or "неизвестная ошибка").strip().splitlines()[-1] if result.stderr else "неизвестная ошибка"
            return MediaFile(path=path, size=size, probe_error=message)

        try:
            data = json.loads(result.stdout or "{}")
        except ValueError as exc:
            return MediaFile(path=path, size=size, probe_error=f"Некорректный JSON: {exc}")

        media = parse_probe_json(data, path)
        if not media.streams:
            media.probe_error = media.probe_error or "Не найдено ни одного потока"
        return media

    def verify_output(self, path: Path) -> VerificationResult:
        """Раздел 54: проверка результата перед переименованием."""
        path = Path(path)
        if not path.exists() or path.stat().st_size == 0:
            return VerificationResult(False, "Выходной файл не создан или пуст.")
        media = self.probe(path)
        if media.probe_error and not media.streams:
            return VerificationResult(False, f"FFprobe не смог прочитать результат: {media.probe_error}")
        return VerificationResult(True, media=media)
