"""Доменная модель приложения.

Разделы ТЗ: 7 (MediaFile), 23 (Job), 44 (формат времени), 53 (статистика).
Модуль не должен зависеть ни от GUI, ни от subprocess.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Раздел 44. Формат времени
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(
    r"^\s*(?:(?:(?P<h>\d+):)?(?P<m>\d{1,2}):)?(?P<s>\d{1,2}(?:[.,]\d+)?)\s*$"
)


def parse_timecode(value: str | float | int | None) -> float | None:
    """Принимает SS, MM:SS, HH:MM:SS, HH:MM:SS.mmm -> float секунд.

    Возвращает None для пустого значения. Бросает ValueError на мусоре.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ValueError("Время не может быть отрицательным")
        return seconds

    text = str(value).strip()
    if not text:
        return None

    match = _TIME_RE.match(text)
    if not match:
        raise ValueError(f"Некорректный формат времени: {value!r}")

    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = float(match.group("s").replace(",", "."))

    if match.group("m") is not None and minutes > 59:
        raise ValueError(f"Некорректное значение минут: {value!r}")
    if match.group("m") is not None and seconds >= 60:
        raise ValueError(f"Некорректное значение секунд: {value!r}")

    return hours * 3600 + minutes * 60 + seconds


def format_timecode(seconds: float | None, with_ms: bool = False) -> str:
    """float секунд -> HH:MM:SS[.mmm]."""
    if seconds is None:
        return "--:--:--"
    seconds = max(0.0, float(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if with_ms:
        return f"{int(hours):02d}:{int(minutes):02d}:{secs:06.3f}"
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def format_size(num_bytes: int | float | None) -> str:
    """Человекочитаемый размер файла."""
    if num_bytes is None:
        return "—"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def format_bitrate(bits_per_second: int | float | None) -> str:
    if not bits_per_second:
        return "—"
    kbps = float(bits_per_second) / 1000.0
    if kbps >= 1000:
        return f"{kbps / 1000:.2f} Mbps"
    return f"{kbps:.0f} kbps"


def parse_bitrate(value: str | int | float | None) -> float | None:
    """'160k' -> 160000.0. Обратна :func:`format_bitrate` по смыслу, но
    разбирает то, что пишут в настройках FFmpeg: 160k, 2M, 4500000."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().rstrip("bps").strip()
    multiplier = 1.0
    if text.endswith("k"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def parse_fraction(value: str | None) -> float | None:
    """'30000/1001' -> 29.97. Возвращает None при 0/0 или мусоре."""
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Раздел 7. Медиа-модель
# ---------------------------------------------------------------------------


class StreamType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    DATA = "data"
    ATTACHMENT = "attachment"
    UNKNOWN = "unknown"


@dataclass
class MediaStream:
    """Один поток внутри контейнера (video/audio/subtitle/...)."""

    index: int
    type: StreamType
    codec_name: str = ""
    codec_long_name: str = ""
    bit_rate: int | None = None
    duration: float | None = None
    disposition: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    # video
    profile: str | None = None
    level: int | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    color_space: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_range: str | None = None
    r_frame_rate: str | None = None
    avg_frame_rate: str | None = None

    # audio
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    sample_fmt: str | None = None

    @property
    def language(self) -> str:
        return self.metadata.get("language", "und")

    @property
    def title(self) -> str:
        return self.metadata.get("title", "")

    @property
    def fps(self) -> float | None:
        return parse_fraction(self.avg_frame_rate) or parse_fraction(self.r_frame_rate)

    @property
    def is_default(self) -> bool:
        return bool(self.disposition.get("default"))

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "—"

    @property
    def is_hdr(self) -> bool:
        """Раздел 41. Эвристика обнаружения HDR."""
        if self.type is not StreamType.VIDEO:
            return False
        transfer = (self.color_transfer or "").lower()
        primaries = (self.color_primaries or "").lower()
        space = (self.color_space or "").lower()
        pix = (self.pix_fmt or "").lower()
        hdr_transfer = transfer in {"smpte2084", "arib-std-b67"}
        wide_gamut = "2020" in primaries or "2020" in space
        ten_bit = any(tag in pix for tag in ("10le", "10be", "12le", "12be", "p010"))
        return hdr_transfer or (wide_gamut and ten_bit)

    @property
    def hdr_kind(self) -> str:
        transfer = (self.color_transfer or "").lower()
        if transfer == "smpte2084":
            return "HDR10 (PQ)"
        if transfer == "arib-std-b67":
            return "HLG"
        if self.is_hdr:
            return "BT.2020 / 10-bit"
        return "SDR"

    def describe(self) -> str:
        if self.type is StreamType.VIDEO:
            fps = self.fps
            fps_text = f"{fps:.3f} fps" if fps else "—"
            return f"{self.codec_name} {self.resolution} {fps_text}"
        if self.type is StreamType.AUDIO:
            rate = f"{self.sample_rate} Hz" if self.sample_rate else "—"
            return f"{self.codec_name} {rate} {self.channel_layout or self.channels or ''}".strip()
        if self.type is StreamType.SUBTITLE:
            return f"{self.codec_name} [{self.language}] {self.title}".strip()
        return self.codec_name or self.type.value


@dataclass
class MediaFile:
    """Результат разбора JSON-вывода ffprobe."""

    path: Path
    format_name: str = ""
    format_long_name: str = ""
    duration: float = 0.0
    size: int = 0
    bitrate: int | None = None
    start_time: float | None = None
    probe_score: int | None = None
    streams: list[MediaStream] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    probe_error: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def is_valid(self) -> bool:
        return self.probe_error is None and bool(self.streams)

    @property
    def video_streams(self) -> list[MediaStream]:
        return [s for s in self.streams if s.type is StreamType.VIDEO]

    @property
    def audio_streams(self) -> list[MediaStream]:
        return [s for s in self.streams if s.type is StreamType.AUDIO]

    @property
    def subtitle_streams(self) -> list[MediaStream]:
        return [s for s in self.streams if s.type is StreamType.SUBTITLE]

    @property
    def primary_video(self) -> MediaStream | None:
        # Обложки в mp3/flac — тоже "video" потоки, отбрасываем их.
        for stream in self.video_streams:
            if not stream.disposition.get("attached_pic"):
                return stream
        return None

    @property
    def primary_audio(self) -> MediaStream | None:
        return self.audio_streams[0] if self.audio_streams else None

    @property
    def has_video(self) -> bool:
        return self.primary_video is not None

    @property
    def has_hdr(self) -> bool:
        return any(s.is_hdr for s in self.video_streams)

    def summary_line(self) -> str:
        """Строка для списка файлов (раздел 5)."""
        parts = [self.name]
        video = self.primary_video
        audio = self.primary_audio
        if video:
            parts.append(video.codec_name.upper())
            parts.append(video.resolution)
        elif audio:
            parts.append(audio.codec_name.upper())
            if audio.sample_rate:
                parts.append(f"{audio.sample_rate / 1000:g} kHz")
        parts.append(format_timecode(self.duration))
        parts.append(format_size(self.size))
        return "   ".join(parts)


# ---------------------------------------------------------------------------
# Раздел 23. Job
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class Operation(str, Enum):
    COMPRESS = "compress"
    CONVERT = "convert"
    TRIM = "trim"


class OverwritePolicy(str, Enum):
    """Раздел 31."""

    OVERWRITE = "overwrite"
    RENAME = "rename"
    ASK = "ask"


class SubtitleMode(str, Enum):
    """Раздел 38."""

    COPY = "copy"
    REMOVE = "remove"
    CONVERT = "convert"
    BURN = "burn"


@dataclass
class VideoOptions:
    """Раздел 36."""

    mode: str = "encode"  # encode | copy | none
    codec: str = "auto"  # логический кодек: h264, hevc, av1...
    encoder: str | None = None  # None => Авто (выбирает CompatibilityService)
    quality_mode: str = "crf"  # crf | bitrate | qp | lossless | size
    crf: float | None = 23.0
    bitrate: str | None = None
    #: Размер выходного файла в мебибайтах (как показывает проводник) для
    #: quality_mode == "size": битрейт считается из него и длительности.
    target_size_mb: float | None = None
    #: Два прохода: первый собирает статистику, второй кодирует. Имеет смысл
    #: только при заданном битрейте — при постоянном качестве (CRF) не нужен.
    two_pass: bool = False
    preset: str | None = "medium"
    tune: str | None = None
    width: int | None = None
    height: int | None = None
    keep_aspect: bool = True
    no_upscale: bool = True
    round_dimensions: bool = True
    fps: float | None = None
    pix_fmt: str | None = None
    profile: str | None = None
    level: str | None = None
    gop: int | None = None
    bframes: int | None = None
    extra_options: dict[str, str] = field(default_factory=dict)


@dataclass
class AudioOptions:
    """Раздел 37."""

    mode: str = "encode"  # encode | copy | none
    codec: str = "auto"
    encoder: str | None = None
    bitrate: str | None = "160k"
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    volume: float | None = None
    #: Приведение громкости к вещательной норме EBU R128 (фильтр loudnorm).
    normalize: bool = False
    extra_options: dict[str, str] = field(default_factory=dict)


@dataclass
class TrimOptions:
    """Разделы 16-17."""

    enabled: bool = False
    start: float | None = None
    end: float | None = None
    duration: float | None = None
    accurate: bool = False  # False => быстрая обрезка (stream copy)

    def effective_duration(self) -> float | None:
        if self.duration is not None:
            return self.duration
        if self.start is not None and self.end is not None:
            return max(0.0, self.end - self.start)
        if self.end is not None:
            return self.end
        return None


@dataclass
class SubtitleOptions:
    mode: SubtitleMode = SubtitleMode.COPY
    encoder: str | None = None
    burn_source: str | None = None
    burn_stream_index: int = 0


@dataclass
class Job:
    """Единица работы очереди. Ровно из этой модели строится команда."""

    input_files: list[str]
    output_file: str
    operation: str = Operation.CONVERT.value

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str = ""
    container: str | None = None  # формат-мультиплексор (mp4, matroska, ...)

    video_encoder: str | None = None
    audio_encoder: str | None = None

    video: VideoOptions = field(default_factory=VideoOptions)
    audio: AudioOptions = field(default_factory=AudioOptions)
    trim: TrimOptions = field(default_factory=TrimOptions)
    subtitles: SubtitleOptions = field(default_factory=SubtitleOptions)

    video_options: dict = field(default_factory=dict)
    audio_options: dict = field(default_factory=dict)
    filters: list[str] = field(default_factory=list)
    audio_filters: list[str] = field(default_factory=list)

    # Раздел 39. Stream mapping. Пустой список => автоматический выбор.
    stream_map: list[str] = field(default_factory=list)
    metadata_mode: str = "copy"  # copy | strip
    custom_metadata: dict[str, str] = field(default_factory=dict)
    input_options: list[str] = field(default_factory=list)
    output_options: list[str] = field(default_factory=list)
    bitstream_filters: dict[str, str] = field(default_factory=dict)
    hwaccel: str | None = None
    overwrite_policy: str = OverwritePolicy.ASK.value
    source: MediaFile | None = None

    status: str = JobStatus.PENDING.value
    progress: float = 0.0
    speed: float | None = None
    eta: float | None = None
    error: str | None = None
    error_category: str | None = None
    command: list[str] = field(default_factory=list)
    log_path: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: "ResultStats | None" = None

    def __post_init__(self) -> None:
        if not self.label:
            self.label = Path(self.input_files[0]).name if self.input_files else self.id

    def clone_for_retry(self) -> "Job":
        """Новое задание с теми же параметрами, готовое к повторной постановке
        в очередь (кнопка «Повторить» на вкладке «Очередь»)."""
        import dataclasses

        return dataclasses.replace(
            self,
            id=uuid.uuid4().hex[:12],
            status=JobStatus.PENDING.value,
            progress=0.0,
            speed=None,
            eta=None,
            error=None,
            error_category=None,
            command=[],
            log_path=None,
            created_at=datetime.now(),
            started_at=None,
            finished_at=None,
            stats=None,
        )

    @property
    def input_path(self) -> Path:
        return Path(self.input_files[0])

    @property
    def output_path(self) -> Path:
        return Path(self.output_file)

    def effective_duration(self) -> float | None:
        """Длительность результата: с учётом обрезки, а не всего исходника."""
        if self.trim.enabled:
            trimmed = self.trim.effective_duration()
            if trimmed:
                return trimmed
        return self.source.duration if self.source else None

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.RUNNING.value, JobStatus.PAUSED.value)

    @property
    def is_finished(self) -> bool:
        return self.status in (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.SKIPPED.value,
        )

    def job_code(self, counter: int) -> str:
        """Идентификатор для логов: JOB-20260829-0001 (раздел 33)."""
        return f"JOB-{self.created_at:%Y%m%d}-{counter:04d}"

    def status_title(self) -> str:
        titles = {
            JobStatus.PENDING.value: "Ожидание",
            JobStatus.RUNNING.value: "Кодирование",
            JobStatus.PAUSED.value: "Пауза",
            JobStatus.COMPLETED.value: "Завершено",
            JobStatus.FAILED.value: "Ошибка",
            JobStatus.CANCELLED.value: "Отменено",
            JobStatus.SKIPPED.value: "Пропущено",
        }
        return titles.get(self.status, self.status)


# ---------------------------------------------------------------------------
# Раздел 26 / 53. Прогресс и статистика
# ---------------------------------------------------------------------------


@dataclass
class ProgressInfo:
    """Разобранный блок `-progress pipe:1`."""

    frame: int | None = None
    fps: float | None = None
    bitrate: str | None = None
    total_size: int | None = None
    out_time_us: int | None = None
    out_time_ms: int | None = None
    out_time: str | None = None
    dup_frames: int | None = None
    drop_frames: int | None = None
    speed: float | None = None
    progress: str | None = None

    processed_seconds: float = 0.0
    fraction: float = 0.0
    eta: float | None = None

    def human(self, total: float | None) -> str:
        left = format_timecode(self.processed_seconds)
        right = format_timecode(total) if total else "--:--:--"
        speed = f"{self.speed:.2f}x" if self.speed else "—"
        eta = format_timecode(self.eta) if self.eta is not None else "—"
        return (
            f"{left} / {right}\n"
            f"Speed: {speed}\n"
            f"ETA: {eta}\n"
            f"Output: {format_size(self.total_size)}"
        )


@dataclass
class ResultStats:
    """Раздел 53."""

    input_size: int = 0
    output_size: int = 0
    processing_time: float = 0.0
    average_speed: float | None = None
    codec: str = ""
    encoder: str = ""
    audio_codec: str = ""

    @property
    def saved_bytes(self) -> int:
        return self.input_size - self.output_size

    @property
    def compression_ratio(self) -> float:
        if not self.input_size:
            return 0.0
        return self.output_size / self.input_size

    @property
    def saved_percent(self) -> float:
        if not self.input_size:
            return 0.0
        return (1.0 - self.compression_ratio) * 100.0

    def report(self) -> str:
        """Статистика результата в виде из раздела 53."""
        speed = f"{self.average_speed:.1f}x" if self.average_speed else "—"
        rows = [
            ("Input", format_size(self.input_size)),
            ("Output", format_size(self.output_size)),
            ("Экономия", f"{self.saved_percent:.1f}%"),
            ("Codec", self.codec or "—"),
            ("Audio", self.audio_codec or "—"),
            ("Time", format_timecode(self.processing_time)),
            ("Average speed", speed),
        ]
        width = max(len(label) for label, _value in rows) + 2
        lines = ["✓ Готово!", ""]
        lines += [f"{label + ':':<{width}}{value}" for label, value in rows]
        return "\n".join(lines)
