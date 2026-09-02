"""Пути, временные файлы, атомарная запись результата.

Разделы ТЗ: 6 (рекурсивный импорт папок с фильтрацией), 30 (запись во
временный файл и переименование при успехе), хранение временных файлов
в ``%TEMP%/FFmpegGUI/{jobs,previews,cache,logs}`` (раздел «Хранение
настроек» README).
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".ogv",
}
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff"}


@dataclass
class ScanOptions:
    """Раздел 6: параметры рекурсивного импорта."""

    recursive: bool = True
    video: bool = True
    audio: bool = True
    images: bool = False


class FilesystemService:
    """Работа с временными файлами и путями результата."""

    def __init__(self, temp_root: Path | None = None) -> None:
        self.temp_root = Path(temp_root) if temp_root else Path(tempfile.gettempdir()) / "FFmpegGUI"

    @property
    def jobs_dir(self) -> Path:
        path = self.temp_root / "jobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def previews_dir(self) -> Path:
        path = self.temp_root / "previews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cache_dir(self) -> Path:
        path = self.temp_root / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = self.temp_root / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_temp(self) -> None:
        """Очищает временные рабочие файлы (не пользовательские логи)."""
        for sub in ("jobs", "previews"):
            directory = self.temp_root / sub
            if not directory.is_dir():
                continue
            for item in directory.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    else:
                        shutil.rmtree(item, ignore_errors=True)
                except OSError as exc:
                    log.debug("Не удалось удалить временный файл %s: %s", item, exc)

    # -- импорт (раздел 6) ---------------------------------------------------
    def scan_folder(self, folder: Path, options: ScanOptions) -> list[Path]:
        folder = Path(folder)
        extensions: set[str] = set()
        if options.video:
            extensions |= VIDEO_EXTENSIONS
        if options.audio:
            extensions |= AUDIO_EXTENSIONS
        if options.images:
            extensions |= IMAGE_EXTENSIONS

        iterator = folder.rglob("*") if options.recursive else folder.glob("*")
        result: list[Path] = []
        for path in iterator:
            if path.is_file() and path.suffix.lower() in extensions:
                result.append(path)
        return sorted(result)

    # -- выходные пути --------------------------------------------------------
    def suggest_output(
        self,
        input_path: Path,
        output_dir: str | Path | None,
        extension: str,
        suffix: str,
    ) -> Path:
        input_path = Path(input_path)
        directory = Path(output_dir) if output_dir else input_path.parent
        extension = extension.lstrip(".")
        return directory / f"{input_path.stem}{suffix}.{extension}"

    def unique_path(self, path: Path) -> Path:
        """Раздел 31: 'file.mp4' -> 'file (2).mp4', если файл существует."""
        path = Path(path)
        if not path.exists():
            return path
        counter = 2
        while True:
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def free_space(self, path: Path) -> int:
        path = Path(path)
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return 0

    # -- временный файл результата (раздел 30) --------------------------------
    def temp_output_for(self, destination: Path) -> Path:
        destination = Path(destination)
        name = f"{uuid.uuid4().hex}{destination.suffix}"
        return self.jobs_dir / name

    def discard(self, path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            log.debug("Не удалось удалить временный файл %s: %s", path, exc)

    def finalize(self, temp_path: Path, destination: Path) -> Path:
        """Переименовывает временный файл в целевой (раздел 30)."""
        temp_path = Path(temp_path)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        try:
            temp_path.replace(destination)
        except OSError:
            shutil.copyfile(temp_path, destination)
            self.discard(temp_path)
        return destination
