"""Извлечение кадров для предпросмотра (раздел 18).

Кадры извлекаются через FFmpeg в PNG во временный каталог; для больших
файлов первое извлечение может занять заметное время (см. «Известные
ограничения» в README).
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path

from .ffmpeg_service import FFmpegService, creation_flags
from .filesystem_service import FilesystemService

log = logging.getLogger(__name__)


class PreviewService:
    """Кадры для предпросмотра таймлайна обрезки."""

    def __init__(self, ffmpeg_service: FFmpegService, filesystem: FilesystemService) -> None:
        self.ffmpeg = ffmpeg_service
        self.filesystem = filesystem

    def extract_frame(self, source: Path, timestamp: float, width: int | None = None) -> Path | None:
        """Возвращает путь к PNG-кадру на секунде `timestamp`, либо None.

        `width` (если задан) масштабирует кадр по ширине для миниатюр —
        высота подбирается автоматически с сохранением пропорций.
        """
        binary = self.ffmpeg.binaries.ffmpeg
        if not binary:
            return None
        output = self.filesystem.previews_dir / f"{uuid.uuid4().hex}.png"
        command = [
            str(binary),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{max(0.0, timestamp):.3f}",
            "-i", str(source),
            "-frames:v", "1",
        ]
        if width:
            command += ["-vf", f"scale={width}:-1"]
        command += [str(output)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=30,
                creationflags=creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Не удалось извлечь кадр предпросмотра: %s", exc)
            return None
        if result.returncode != 0 or not output.exists():
            return None
        return output

    def extract_thumbnail_strip(
        self, source: Path, duration: float, count: int = 6, width: int = 140
    ) -> list[tuple[float, Path]]:
        """Раздел «Миниатюры на обрезке»: `count` равномерно распределённых
        по длительности кадров-превью. Каждый кадр — отдельный быстрый вызов
        FFmpeg с seek до входа (`-ss` перед `-i`), что даёт переход к
        ближайшему ключевому кадру без полного декодирования файла.
        """
        if duration <= 0 or count <= 0:
            return []
        results: list[tuple[float, Path]] = []
        for index in range(count):
            timestamp = duration * index / count
            frame = self.extract_frame(source, timestamp, width=width)
            if frame is not None:
                results.append((timestamp, frame))
        return results
