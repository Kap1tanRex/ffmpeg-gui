"""Встроенные и пользовательские профили сжатия (раздел 14 / 20).

Профиль — это набор значений :class:`VideoOptions`/`AudioOptions`,
применяемых к :class:`Job` одним кликом. Пользовательские профили
хранятся как JSON-файлы в каталоге настроек и могут быть созданы из
текущего задания.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import AudioOptions, Job, VideoOptions

log = logging.getLogger(__name__)


@dataclass
class Profile:
    name: str
    description: str = ""
    video: VideoOptions = field(default_factory=VideoOptions)
    audio: AudioOptions = field(default_factory=AudioOptions)
    container: str | None = None
    builtin: bool = False

    def apply(self, job: Job) -> Job:
        job.video = VideoOptions(**asdict(self.video))
        job.audio = AudioOptions(**asdict(self.audio))
        if self.container:
            job.container = self.container
        return job

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "video": asdict(self.video),
            "audio": asdict(self.audio),
            "container": self.container,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        return cls(
            name=data.get("name", "Без имени"),
            description=data.get("description", ""),
            video=VideoOptions(**data.get("video", {})),
            audio=AudioOptions(**data.get("audio", {})),
            container=data.get("container"),
        )

    @classmethod
    def from_job(cls, name: str, job: Job, description: str = "") -> "Profile":
        return cls(
            name=name,
            description=description,
            video=VideoOptions(**asdict(job.video)),
            audio=AudioOptions(**asdict(job.audio)),
            container=job.container,
        )


def _builtin_profiles() -> list[Profile]:
    """Раздел 14: архив, максимальное качество, баланс, малый размер, телефон, мессенджер."""
    return [
        Profile(
            "Архив",
            "Максимальное сохранение качества для долговременного хранения.",
            VideoOptions(codec="hevc", quality_mode="crf", crf=16.0, preset="slow"),
            AudioOptions(codec="flac", bitrate=None),
            container="matroska",
            builtin=True,
        ),
        Profile(
            "Максимальное качество",
            "Высокое качество, размер файла вторичен.",
            VideoOptions(codec="h264", quality_mode="crf", crf=18.0, preset="slow"),
            AudioOptions(codec="aac", bitrate="256k"),
            container="matroska",
            builtin=True,
        ),
        Profile(
            "Баланс",
            "Разумный компромисс между качеством и размером.",
            VideoOptions(codec="h264", quality_mode="crf", crf=23.0, preset="medium"),
            AudioOptions(codec="aac", bitrate="160k"),
            container="mp4",
            builtin=True,
        ),
        Profile(
            "Малый размер",
            "Минимальный размер файла при приемлемом качестве.",
            VideoOptions(codec="hevc", quality_mode="crf", crf=30.0, preset="fast"),
            AudioOptions(codec="aac", bitrate="96k"),
            container="mp4",
            builtin=True,
        ),
        Profile(
            "Телефон",
            "Совместимо с большинством мобильных устройств.",
            VideoOptions(
                codec="h264", quality_mode="crf", crf=24.0, preset="fast",
                width=1280, height=720, keep_aspect=True,
            ),
            AudioOptions(codec="aac", bitrate="128k"),
            container="mp4",
            builtin=True,
        ),
        Profile(
            "Мессенджер",
            "Небольшой файл для отправки через мессенджеры.",
            VideoOptions(
                codec="h264", quality_mode="crf", crf=28.0, preset="fast",
                width=854, height=480, keep_aspect=True,
            ),
            AudioOptions(codec="aac", bitrate="96k"),
            container="mp4",
            builtin=True,
        ),
    ]


class ProfileManager:
    """Хранит встроенные профили и загружает пользовательские из каталога."""

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self.profiles_dir = Path(profiles_dir) if profiles_dir else None
        self._builtin: dict[str, Profile] = {p.name: p for p in _builtin_profiles()}
        self._custom: dict[str, Profile] = {}
        self.reload()

    def reload(self) -> None:
        self._custom.clear()
        if not self.profiles_dir or not self.profiles_dir.is_dir():
            return
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profile = Profile.from_dict(data)
            except (OSError, ValueError, TypeError) as exc:
                log.warning("Профиль %s не загружен: %s", path.name, exc)
                continue
            self._custom[profile.name] = profile

    def list(self) -> list[Profile]:
        return list(self._builtin.values()) + list(self._custom.values())

    def get(self, name: str) -> Profile | None:
        return self._custom.get(name) or self._builtin.get(name)

    def _path_for(self, name: str) -> Path | None:
        """Имя файла профиля. Из имени остаются только буквы, цифры, пробел,
        дефис и подчёркивание — разделители пути в имя не попадают."""
        if not self.profiles_dir:
            return None
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
        return self.profiles_dir / f"{safe_name or 'profile'}.json"

    def save(self, profile: Profile) -> Path | None:
        path = self._path_for(profile.name)
        if path is None:
            return None
        try:
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Профиль %s не сохранён: %s", profile.name, exc)
            return None
        self._custom[profile.name] = profile
        return path

    def delete(self, name: str) -> bool:
        path = self._path_for(name)
        if path is None or name not in self._custom:
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Профиль %s не удалён: %s", name, exc)
            return False
        del self._custom[name]
        return True
