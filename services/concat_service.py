"""Склейка нескольких файлов в один.

Есть два пути, и разница между ними для пользователя огромная:

* **Без перекодирования** — годится, когда все куски сняты одним и тем же
  устройством: те же кодеки, размер кадра и параметры звука. FFmpeg просто
  дописывает пакеты один за другим, поэтому час видео склеивается за секунды
  и без единой потери качества.
* **С перекодированием** — когда куски разные. Работает всегда, но занимает
  столько же времени, сколько обычное кодирование.

Модуль решает, какой путь возможен, и готовит список файлов для первого.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..core.models import MediaFile, StreamType


def _video_signature(media: MediaFile) -> tuple:
    stream = next((s for s in media.streams if s.type is StreamType.VIDEO), None)
    if stream is None:
        return ()
    return (stream.codec_name, stream.width, stream.height, stream.pix_fmt)


def _audio_signature(media: MediaFile) -> tuple:
    stream = next((s for s in media.streams if s.type is StreamType.AUDIO), None)
    if stream is None:
        return ()
    return (stream.codec_name, stream.sample_rate, stream.channels)


def can_copy(medias: list[MediaFile]) -> bool:
    """Можно ли склеить без перекодирования.

    Требуется совпадение кодеков и параметров: иначе получится файл, который
    рассыпается на границе кусков — или не открывается вовсе.
    """
    if len(medias) < 2:
        return False
    first_video = _video_signature(medias[0])
    first_audio = _audio_signature(medias[0])
    if not first_video:
        return False
    return all(
        _video_signature(media) == first_video and _audio_signature(media) == first_audio
        for media in medias[1:]
    )


def has_audio_everywhere(medias: list[MediaFile]) -> bool:
    """Есть ли звук во всех кусках — фильтру concat нужно знать заранее."""
    return bool(medias) and all(
        any(s.type is StreamType.AUDIO for s in media.streams) for media in medias
    )


def write_list(paths: list[Path | str], directory: Path) -> Path:
    """Готовит список для демультиплексора concat.

    Одинарные кавычки внутри пути экранируются так, как того требует сам
    формат списка: ``'`` превращается в ``'\\''``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"concat_{uuid.uuid4().hex[:8]}.txt"
    lines = []
    for path in paths:
        escaped = str(Path(path).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
