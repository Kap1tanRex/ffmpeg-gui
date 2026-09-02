"""Поиск и запуск бинарников FFmpeg/FFprobe (раздел 46).

Порядок поиска:
1. путь, указанный пользователем в настройках;
2. каталог приложения и подкаталог ``ffmpeg/bin``;
3. переменная окружения ``PATH``;
4. стандартные каталоги системы.

Ничего не загружается из сети — только поиск уже установленной сборки.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_APP_ROOT = Path(__file__).resolve().parent.parent

_STANDARD_DIRS_WINDOWS = [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]
_STANDARD_DIRS_POSIX = ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/snap/bin"]

_FFMPEG_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
_FFPROBE_NAME = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"


def creation_flags() -> int:
    """Флаг для subprocess, чтобы не мелькало консольное окно на Windows."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return 0


@dataclass
class Binaries:
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    ffmpeg_version: str = ""
    ffprobe_version: str = ""

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None and self.ffprobe is not None


def _version_of(binary: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Не удалось получить версию %s: %s", binary, exc)
        return ""
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    return first_line.replace("ffmpeg version ", "").replace("ffprobe version ", "").split(" ")[0]


def _app_roots() -> list[Path]:
    """Каталоги «рядом с приложением».

    В собранном .exe (onefile) пакет распакован во временный каталог, а у
    пользователя на диске лежит только сам файл программы — комплектный
    ffmpeg надо искать возле него, иначе «положить ffmpeg рядом» не работает.
    """
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(_APP_ROOT)
    return roots


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    for root in _app_roots():
        dirs += [root / "ffmpeg" / "bin", root]
    if sys.platform == "win32":
        dirs += [Path(p) for p in _STANDARD_DIRS_WINDOWS]
    else:
        dirs += [Path(p) for p in _STANDARD_DIRS_POSIX]
    return dirs


def _find_binary(name: str, user_path: str | None) -> Path | None:
    if user_path:
        candidate = Path(user_path)
        if candidate.is_dir():
            candidate = candidate / name
        if candidate.is_file():
            return candidate

    found = shutil.which(name)
    if found:
        return Path(found)

    for directory in _candidate_dirs():
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


class FFmpegService:
    """Локализация бинарников и низкоуровневые вызовы FFmpeg."""

    def __init__(self, ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> None:
        self.binaries = Binaries()
        self.locate(ffmpeg_path, ffprobe_path)

    @property
    def available(self) -> bool:
        return self.binaries.available

    def locate(self, ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> Binaries:
        """Ищет бинарники по правилам раздела 46 и обновляет self.binaries."""
        ffmpeg = _find_binary(_FFMPEG_NAME, ffmpeg_path)
        ffprobe = _find_binary(_FFPROBE_NAME, ffprobe_path or (str(ffmpeg.parent) if ffmpeg else None))

        binaries = Binaries(ffmpeg=ffmpeg, ffprobe=ffprobe)
        if ffmpeg:
            binaries.ffmpeg_version = _version_of(ffmpeg)
        if ffprobe:
            binaries.ffprobe_version = _version_of(ffprobe)
        self.binaries = binaries
        return binaries

    # ------------------------------------------------------------------
    def query(self, option: str) -> str:
        """Запускает `ffmpeg <option>` и возвращает stdout+stderr."""
        if not self.binaries.ffmpeg:
            return ""
        try:
            result = subprocess.run(
                [str(self.binaries.ffmpeg), *option.split()],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=creation_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Запрос FFmpeg %s не удался: %s", option, exc)
            return ""
        return (result.stdout or "") + (result.stderr or "")

    def encoder_help(self, encoder: str) -> str:
        """Раздел 21: `ffmpeg -h encoder=NAME`."""
        return self.query(f"-h encoder={encoder}")

    def probe_command(self) -> list[str]:
        return [str(self.binaries.ffprobe)] if self.binaries.ffprobe else []


@dataclass
class SystemInfo:
    """Раздел 47: содержимое диагностического отчёта."""

    app_version: str = ""
    ffmpeg_version: str = ""
    ffprobe_version: str = ""
    ffmpeg_path: str = ""
    build_configuration: str = ""
    encoders_count: int = 0
    hwaccels: list[str] = field(default_factory=list)
    free_disk_space: int = 0
    recent_errors: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        from ..core.models import format_size

        lines = [
            f"FFmpeg GUI {self.app_version}",
            "",
            f"FFmpeg: {self.ffmpeg_version or 'не найден'}",
            f"FFprobe: {self.ffprobe_version or 'не найден'}",
            f"Путь: {self.ffmpeg_path or '—'}",
            f"Конфигурация сборки: {self.build_configuration or '—'}",
            f"Энкодеров обнаружено: {self.encoders_count}",
            f"Аппаратное ускорение: {', '.join(self.hwaccels) or '—'}",
            f"Свободно места: {format_size(self.free_disk_space)}",
        ]
        if self.recent_errors:
            lines.append("")
            lines.append("Последние ошибки:")
            lines.extend(f"  - {error}" for error in self.recent_errors)
        return "\n".join(lines)
