"""Совместимость кодеков, энкодеров и контейнеров.

Раздел ТЗ 10 (Compatibility Service), 15 (Stream Copy analysis).

Список известных соответствий контейнер/кодек — вспомогательная эвристика
для предупреждений; окончательное решение всегда за самим FFmpeg. Список
логических кодеков и энкодеров не статичен: он строится из
:class:`~ffmpeg_gui.core.capabilities.FFmpegCapabilities`, полученного
опросом установленного FFmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .capabilities import FFmpegCapabilities
from .models import MediaFile, StreamType

# Расширение по умолчанию для распространённых мультиплексоров.
_MUXER_EXTENSIONS: dict[str, str] = {
    "matroska": "mkv",
    "mp4": "mp4",
    "mov": "mov",
    "ipod": "m4v",
    "webm": "webm",
    "avi": "avi",
    "flv": "flv",
    "mpegts": "ts",
    "ogg": "ogv",
    "mp3": "mp3",
    "adts": "aac",
    "flac": "flac",
    "wav": "wav",
    "ogg_audio": "ogg",
}

# Контейнер, который приложение считает аудио-только.
_AUDIO_ONLY_CONTAINERS = {"mp3", "adts", "flac", "wav", "ogg_audio", "oga"}

# Грубая таблица допустимости видеокодека в контейнере (эвристика, не запрет).
_CONTAINER_VIDEO_CODECS: dict[str, set[str]] = {
    "mp4": {"h264", "hevc", "av1", "mpeg4", "mpeg2video", "vp9"},
    "ipod": {"h264", "hevc"},
    "mov": {"h264", "hevc", "av1", "prores", "mjpeg"},
    "matroska": set(),  # matroska принимает практически всё
    "webm": {"vp8", "vp9", "av1"},
    "avi": {"h264", "mpeg4", "mjpeg", "mpeg2video"},
    "flv": {"h264", "flv1"},
    "mpegts": {"h264", "hevc", "mpeg2video"},
}

_CONTAINER_AUDIO_CODECS: dict[str, set[str]] = {
    "mp4": {"aac", "ac3", "eac3", "mp3", "alac"},
    "ipod": {"aac", "alac"},
    "mov": {"aac", "alac", "pcm_s16le", "pcm_s24le"},
    "matroska": set(),
    "webm": {"opus", "vorbis"},
    "avi": {"mp3", "ac3", "pcm_s16le"},
    "flv": {"aac", "mp3"},
    "mpegts": {"aac", "mp3", "ac3"},
}

_DEFAULT_CODECS: dict[str, tuple[str, ...]] = {
    "video": ("h264", "hevc", "mpeg4", "vp9", "av1"),
    "audio": ("aac", "mp3", "opus", "vorbis"),
}

_SUBTITLE_CODEC_BY_MUXER: dict[str, str] = {
    "mp4": "mov_text",
    "ipod": "mov_text",
    "mov": "mov_text",
    "matroska": "ass",
    "webm": "webvtt",
}


@dataclass
class StreamCopyDecision:
    """Раздел 15: можно ли выполнить ремукс без перекодирования."""

    video_copy_ok: bool = True
    audio_copy_ok: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def full_remux(self) -> bool:
        return self.video_copy_ok and self.audio_copy_ok and not self.reasons


class CompatibilityService:
    """Логика совместимости, построенная поверх реальных capabilities."""

    def __init__(self, capabilities: FFmpegCapabilities | None = None) -> None:
        self.caps = capabilities or FFmpegCapabilities()

    # -- контейнеры ---------------------------------------------------------
    def extension_for_muxer(self, muxer: str) -> str:
        if muxer in _MUXER_EXTENSIONS:
            return _MUXER_EXTENSIONS[muxer]
        info = self.caps.muxers.get(muxer)
        if info is not None and info.names:
            return info.names[0]
        return muxer

    def muxer_for_extension(self, suffix: str) -> str | None:
        extension = suffix.lstrip(".").lower()
        if not extension:
            return None
        for muxer, ext in _MUXER_EXTENSIONS.items():
            if ext == extension:
                return muxer
        for info in self.caps.muxers.values():
            if extension in info.names:
                # FFmpeg группирует алиасы одного мультиплексора через запятую
                # (например, muxer "mov,mp4,m4a,3gp,3g2,mj2"); возвращаем
                # конкретное короткое имя расширения, а не весь список — иначе
                # `-f <весь список>` будет отвергнут самим FFmpeg.
                return extension
        return None

    def is_audio_only(self, container: str) -> bool:
        return container in _AUDIO_ONLY_CONTAINERS

    def container_accepts_video(self, container: str, codec: str) -> bool:
        allowed = _CONTAINER_VIDEO_CODECS.get(container)
        if not allowed:
            return True
        return codec in allowed

    def container_accepts_audio(self, container: str, codec: str) -> bool:
        allowed = _CONTAINER_AUDIO_CODECS.get(container)
        if not allowed:
            return True
        return codec in allowed

    def default_subtitle_codec(self, container: str) -> str | None:
        return _SUBTITLE_CODEC_BY_MUXER.get(container)

    # -- кодеки / энкодеры ----------------------------------------------------
    def resolve_encoder(
        self, codec: str, media_type: str, prefer_hw_suffixes: tuple[str, ...] | list[str] | None = None
    ) -> str | None:
        """Раздел 10: логический кодек -> лучший доступный энкодер.

        По умолчанию предпочитает программные энкодеры аппаратным.
        Если передан ``prefer_hw_suffixes`` (например, ``("nvenc",)`` для
        обнаруженной видеокарты NVIDIA — раздел 35), сначала ищется
        аппаратный энкодер с одним из этих суффиксов, и только если такого
        нет — используется обычный порядок (программный энкодер).
        ``codec="auto"`` перебирает разумные варианты по умолчанию и
        возвращает первый, для которого в этой сборке FFmpeg есть энкодер.
        """
        if codec in ("", None):
            return None
        if codec == "auto":
            for candidate in _DEFAULT_CODECS.get(media_type, ()):
                encoder = self.resolve_encoder(candidate, media_type, prefer_hw_suffixes)
                if encoder:
                    return encoder
            return None
        candidates = self.caps.encoders_for_codec(codec)
        if not candidates:
            # capabilities ещё не готовы (например, тесты без FFmpeg):
            # возвращаем кодек как есть, если он сам похож на имя энкодера.
            if self.caps.has_encoder(codec):
                return codec
            return None
        if prefer_hw_suffixes:
            for suffix in prefer_hw_suffixes:
                for candidate in candidates:
                    if candidate.is_hardware and candidate.name.endswith(f"_{suffix}"):
                        return candidate.name
        software = [c for c in candidates if not c.is_hardware]
        chosen = software[0] if software else candidates[0]
        return chosen.name

    def codec_of_encoder(self, encoder: str | None) -> str:
        if not encoder:
            return ""
        info = self.caps.encoders.get(encoder)
        if info is not None and info.codec:
            return info.codec
        from .capabilities import _guess_codec

        return _guess_codec(encoder)

    def decoder_available(self, codec_name: str) -> bool:
        if not self.caps.decoders:
            return True
        if codec_name in self.caps.decoders:
            return True
        codec_info = self.caps.codecs.get(codec_name)
        return bool(codec_info and codec_info.decoding)

    def supports_crf(self, encoder: str) -> bool:
        return encoder.startswith(("libx264", "libx265", "libsvtav1", "libaom", "librav1e", "libvpx"))

    # -- stream copy (раздел 15) ---------------------------------------------
    def analyze_copy(
        self, media: MediaFile, container: str, accurate_trim: bool = False
    ) -> StreamCopyDecision:
        decision = StreamCopyDecision()
        video = media.primary_video
        audio = media.primary_audio

        if video is not None:
            if not self.container_accepts_video(container, video.codec_name):
                decision.video_copy_ok = False
                decision.reasons.append(
                    f"Видеокодек {video.codec_name} несовместим с контейнером {container}."
                )
        if audio is not None:
            if not self.container_accepts_audio(container, audio.codec_name):
                decision.audio_copy_ok = False
                decision.reasons.append(
                    f"Аудиокодек {audio.codec_name} несовместим с контейнером {container}."
                )
        if accurate_trim:
            # Точная обрезка требует перекодирования по определению.
            decision.video_copy_ok = False
            decision.reasons.append("Точная обрезка требует перекодирования видео.")
        return decision

    def stream_summary(self, media: MediaFile) -> str:
        """Короткое описание потоков файла (используется в GUI)."""
        parts: list[str] = []
        for stream in media.streams:
            if stream.type is StreamType.VIDEO and stream.disposition.get("attached_pic"):
                continue
            parts.append(stream.describe())
        return "; ".join(parts)
