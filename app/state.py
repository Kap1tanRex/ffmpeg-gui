"""Настройки приложения (``config.json`` с миграцией) и состояние сессии.

Раздел «Хранение настроек» README:

| ОС | Каталог |
|---|---|
| Windows | ``%APPDATA%\\FFmpegGUI`` |
| Linux | ``~/.config/FFmpegGUI`` |
| macOS | ``~/Library/Application Support/FFmpegGUI`` |

Неизвестные и повреждённые значения заменяются значениями по умолчанию,
а не приводят к падению приложения.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, fields

from ..core.naming import DEFAULT_TEMPLATE
from pathlib import Path

log = logging.getLogger(__name__)

APP_VERSION = "1.6.0"
SETTINGS_VERSION = 1

#: Репозиторий обновлений: пока не задан, проверка при запуске молчит.
DEFAULT_REPOSITORY = ""

#: Источник сборок FFmpeg по умолчанию (см. services.ffmpeg_updater).
SOURCE_GYAN = "gyan"


def _default_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "FFmpegGUI"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FFmpegGUI"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "FFmpegGUI"


@dataclass
class Settings:
    """Пользовательские настройки; сериализуется в ``config.json``."""

    version: int = SETTINGS_VERSION
    language: str = "ru"
    theme: str = "System"
    ui_scale: float = 1.0
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    output_directory: str = ""
    overwrite_policy: str = "ask"  # overwrite | rename | ask
    parallel_jobs: int = 1
    recursive_import: bool = True
    import_video: bool = True
    import_audio: bool = True
    import_images: bool = False
    auto_probe: bool = True
    last_profile: str = ""
    output_template: str = DEFAULT_TEMPLATE
    show_tooltips: bool = True
    auto_hardware_encoding: bool = False
    hardware_decoding: bool = False
    watch_folder: str = ""
    watch_enabled: bool = False
    default_crf: float = 23.0
    show_all_codecs: bool = False
    check_updates_on_start: bool = True
    update_repository: str = DEFAULT_REPOSITORY
    update_prereleases: bool = False
    ffmpeg_source: str = SOURCE_GYAN

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        """Собирает Settings из словаря, отбрасывая неизвестные/битые поля."""
        known_fields = {f.name for f in fields(cls)}
        defaults = cls()
        clean: dict = {}
        for name in known_fields:
            if name not in data:
                continue
            value = data[name]
            default_value = getattr(defaults, name)
            if isinstance(default_value, float) and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if not isinstance(value, type(default_value)):
                continue
            clean[name] = value
        clean["version"] = SETTINGS_VERSION
        return cls(**clean)


class SettingsStore:
    """Загрузка/сохранение ``config.json`` и производные пути кэша/логов."""

    def __init__(self, settings_dir: Path | None = None) -> None:
        self.directory = Path(settings_dir) if settings_dir else _default_config_dir()

    @property
    def config_path(self) -> Path:
        return self.directory / "config.json"

    @property
    def cache_path(self) -> Path:
        return self.directory / "capabilities.json"

    @property
    def profiles_dir(self) -> Path:
        return self.directory / "profiles"

    @property
    def logs_dir(self) -> Path:
        return self.directory / "logs"

    def load(self) -> Settings:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Не удалось создать каталог настроек: %s", exc)

        if not self.config_path.is_file():
            return Settings()
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("config.json повреждён, используются значения по умолчанию: %s", exc)
            return Settings()
        if not isinstance(data, dict):
            return Settings()
        return Settings.from_dict(data)

    def save(self, settings: Settings) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Не удалось сохранить настройки: %s", exc)


class AppState:
    """Список импортированных файлов и текущий выбор (сессия, не сохраняется)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.files: list = []
        self.selected_index: int = -1

    def add_file(self, media) -> bool:
        if any(existing.path == media.path for existing in self.files):
            return False
        self.files.append(media)
        return True

    def remove_at(self, index: int) -> bool:
        if not 0 <= index < len(self.files):
            return False
        del self.files[index]
        if self.selected_index >= len(self.files):
            self.selected_index = len(self.files) - 1
        return True

    def select(self, index: int):
        if 0 <= index < len(self.files):
            self.selected_index = index
            return self.files[index]
        self.selected_index = -1
        return None

    def clear(self) -> None:
        self.files.clear()
        self.selected_index = -1
