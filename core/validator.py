"""Проверка Job до запуска FFmpeg.

Раздел ТЗ 28. Pipeline: Job → Validator → Command Builder → FFmpeg.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .capabilities import FFmpegCapabilities
from .compatibility import CompatibilityService
from .models import Job, SubtitleMode, format_size

# Минимальный запас на диске сверх ожидаемого размера результата.
DISK_SAFETY_MARGIN = 128 * 1024 * 1024


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: Severity = Severity.ERROR
    hint: str = ""

    def __str__(self) -> str:
        return self.message


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.WARNING]

    @property
    def infos(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity is Severity.INFO]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, message: str, severity: Severity = Severity.ERROR, hint: str = "") -> None:
        self.issues.append(ValidationIssue(code, message, severity, hint))

    def text(self) -> str:
        return "\n".join(f"[{issue.severity.value}] {issue.message}" for issue in self.issues)


class Validator:
    """Набор проверок из раздела 28."""

    def __init__(self, capabilities: FFmpegCapabilities | None = None) -> None:
        self.caps = capabilities or FFmpegCapabilities()
        self.compat = CompatibilityService(self.caps)

    def validate(self, job: Job) -> ValidationResult:
        result = ValidationResult()
        self._check_input(job, result)
        self._check_output(job, result)
        self._check_disk_space(job, result)
        self._check_encoders(job, result)
        self._check_decoders(job, result)
        self._check_parameters(job, result)
        self._check_container(job, result)
        self._check_stream_copy(job, result)
        self._check_trim(job, result)
        return result

    # -- проверки ---------------------------------------------------------
    @staticmethod
    def _check_input(job: Job, result: ValidationResult) -> None:
        if not job.input_files:
            result.add("NO_INPUT", "Не выбран входной файл.")
            return
        for input_file in job.input_files:
            path = Path(input_file)
            if not path.exists():
                result.add("INPUT_MISSING", f"Входной файл не найден: {path}")
            elif path.is_dir():
                result.add("INPUT_IS_DIR", f"Указан каталог, а не файл: {path}")
            elif path.stat().st_size == 0:
                result.add("INPUT_EMPTY", f"Входной файл пуст: {path.name}")
        if job.source is not None and job.source.probe_error:
            result.add(
                "INPUT_CORRUPTED",
                f"FFprobe не смог прочитать файл: {job.source.probe_error}",
            )

    @staticmethod
    def _check_output(job: Job, result: ValidationResult) -> None:
        if not job.output_file:
            result.add("NO_OUTPUT", "Не указан выходной файл.")
            return
        output = job.output_path
        directory = output.parent
        if not directory.exists():
            result.add("OUTPUT_DIR_MISSING", f"Каталог назначения не существует: {directory}")
        elif not directory.is_dir():
            result.add("OUTPUT_DIR_INVALID", f"Путь назначения не является каталогом: {directory}")
        else:
            probe_file = directory / ".ffmpeg_gui_write_test"
            try:
                probe_file.touch()
                probe_file.unlink()
            except OSError:
                result.add(
                    "OUTPUT_PERMISSION",
                    f"Нет прав на запись в каталог: {directory}",
                )

        for input_file in job.input_files:
            try:
                same = Path(input_file).resolve() == output.resolve()
            except OSError:
                same = str(Path(input_file)) == str(output)
            if same:
                result.add(
                    "SAME_PATH",
                    "Входной и выходной файлы совпадают. Укажите другое имя результата.",
                )

        if output.exists():
            result.add(
                "OUTPUT_EXISTS",
                f"Файл {output.name} уже существует.",
                Severity.WARNING,
                hint="Действие определяется настройкой перезаписи.",
            )

    @staticmethod
    def _check_disk_space(job: Job, result: ValidationResult) -> None:
        if not job.output_file:
            return
        directory = job.output_path.parent
        if not directory.exists():
            return
        try:
            free = shutil.disk_usage(directory).free
        except OSError:
            return
        source_size = job.source.size if job.source else 0
        required = min(source_size, 4 * 1024 ** 3) + DISK_SAFETY_MARGIN
        if free < required:
            result.add(
                "DISK_FULL",
                f"Недостаточно места на диске: доступно {format_size(free)}.",
                Severity.WARNING,
            )

    def _check_encoders(self, job: Job, result: ValidationResult) -> None:
        if job.video.mode == "encode":
            encoder = job.video_encoder or job.video.encoder or self.compat.resolve_encoder(
                job.video.codec, "video"
            )
            if not encoder:
                result.add(
                    "MISSING_ENCODER",
                    f"В этой сборке FFmpeg нет энкодера для кодека «{job.video.codec}».",
                )
            elif not self.caps.has_encoder(encoder) and self.caps.encoders:
                result.add("MISSING_ENCODER", f"Видеоэнкодер «{encoder}» недоступен.")
            elif encoder and self.caps.encoders.get(encoder) and self.caps.encoders[encoder].is_hardware:
                result.add(
                    "HARDWARE_ENCODER",
                    f"Выбран аппаратный энкодер {encoder}. "
                    "Наличие энкодера не гарантирует поддержку конкретным GPU и драйвером.",
                    Severity.INFO,
                )

        if job.audio.mode == "encode":
            encoder = job.audio_encoder or job.audio.encoder or self.compat.resolve_encoder(
                job.audio.codec, "audio"
            )
            if not encoder:
                result.add(
                    "MISSING_ENCODER",
                    f"В этой сборке FFmpeg нет энкодера для кодека «{job.audio.codec}».",
                )
            elif not self.caps.has_encoder(encoder) and self.caps.encoders:
                result.add("MISSING_ENCODER", f"Аудиоэнкодер «{encoder}» недоступен.")

    def _check_decoders(self, job: Job, result: ValidationResult) -> None:
        source = job.source
        if source is None or not self.caps.decoders:
            return
        for stream in source.streams:
            if stream.type.value not in ("video", "audio"):
                continue
            if stream.codec_name and not self.compat.decoder_available(stream.codec_name):
                result.add(
                    "MISSING_DECODER",
                    f"Нет декодера для потока #{stream.index} ({stream.codec_name}).",
                )

    @staticmethod
    def _check_parameters(job: Job, result: ValidationResult) -> None:
        video = job.video
        if video.mode == "encode":
            if video.quality_mode == "crf" and video.crf is not None:
                if not 0 <= video.crf <= 63:
                    result.add(
                        "INVALID_ARGUMENT",
                        f"Значение CRF {video.crf:g} вне допустимого диапазона 0–63.",
                    )
            if video.quality_mode == "bitrate" and not video.bitrate:
                result.add("INVALID_ARGUMENT", "Выбран режим битрейта, но битрейт не задан.")
            if video.quality_mode == "size":
                if not video.target_size_mb or video.target_size_mb <= 0:
                    result.add(
                        "INVALID_ARGUMENT",
                        "Выбран режим целевого размера, но размер не задан.",
                    )
                elif job.effective_duration() is None:
                    result.add(
                        "INVALID_ARGUMENT",
                        "Неизвестна длительность исходника — размер не пересчитать в битрейт.",
                    )
            for name, value in (("ширина", video.width), ("высота", video.height)):
                if value is not None and value <= 0:
                    result.add("INVALID_ARGUMENT", f"Некорректная {name} кадра: {value}.")
            if video.fps is not None and not 0 < video.fps <= 1000:
                result.add("INVALID_ARGUMENT", f"Некорректный FPS: {video.fps:g}.")

        audio = job.audio
        if audio.mode == "encode":
            if audio.channels is not None and not 1 <= audio.channels <= 16:
                result.add("INVALID_ARGUMENT", f"Некорректное число каналов: {audio.channels}.")
            if audio.sample_rate is not None and audio.sample_rate <= 0:
                result.add("INVALID_ARGUMENT", "Некорректная частота дискретизации.")

        if job.video.mode == "none" and job.audio.mode == "none":
            result.add("INVALID_ARGUMENT", "Отключены и видео, и аудио — результат будет пустым.")

    def _check_container(self, job: Job, result: ValidationResult) -> None:
        container = job.container
        if not container:
            return
        if self.caps.muxers and not self.caps.has_muxer(container):
            result.add(
                "UNSUPPORTED_CONTAINER",
                f"Контейнер «{container}» не поддерживается этой сборкой FFmpeg.",
            )
            return

        source = job.source
        if job.video.mode == "copy" and source is not None and source.primary_video:
            codec = source.primary_video.codec_name
            if not self.compat.container_accepts_video(container, codec):
                result.add(
                    "UNSUPPORTED_CODEC",
                    f"Контейнер {container} не принимает видеокодек {codec} без перекодирования.",
                )
        if job.video.mode == "encode":
            encoder = job.video_encoder or job.video.encoder
            codec = self.compat.codec_of_encoder(encoder) if encoder else job.video.codec
            if codec and not self.compat.container_accepts_video(container, codec):
                result.add(
                    "UNSUPPORTED_CODEC",
                    f"Контейнер {container} обычно не принимает видеокодек {codec}.",
                    Severity.WARNING,
                )

        if job.audio.mode == "copy" and source is not None and source.primary_audio:
            codec = source.primary_audio.codec_name
            if not self.compat.container_accepts_audio(container, codec):
                result.add(
                    "UNSUPPORTED_CODEC",
                    f"Контейнер {container} не принимает аудиокодек {codec} без перекодирования.",
                )

        if job.subtitles.mode is SubtitleMode.COPY and source is not None and source.subtitle_streams:
            if container in ("mp4", "ipod"):
                result.add(
                    "SUBTITLE_COMPAT",
                    "MP4 поддерживает ограниченный набор форматов субтитров; "
                    "может потребоваться конвертация в mov_text.",
                    Severity.WARNING,
                )

    def _check_stream_copy(self, job: Job, result: ValidationResult) -> None:
        source = job.source
        if source is None or not job.container:
            return
        if job.video.mode != "copy" and job.audio.mode != "copy":
            decision = self.compat.analyze_copy(source, job.container, job.trim.accurate)
            if decision.full_remux and job.operation == "convert":
                result.add(
                    "STREAM_COPY_POSSIBLE",
                    "Потоки совместимы с выбранным контейнером — "
                    "можно выполнить ремукс без перекодирования (быстрее и без потерь).",
                    Severity.INFO,
                )

    @staticmethod
    def _check_trim(job: Job, result: ValidationResult) -> None:
        trim = job.trim
        if not trim.enabled:
            return
        duration = job.source.duration if job.source else None
        if trim.start is not None and trim.start < 0:
            result.add("INVALID_ARGUMENT", "Начало обрезки отрицательное.")
        if trim.start is not None and trim.end is not None and trim.end <= trim.start:
            result.add("INVALID_ARGUMENT", "Конец обрезки должен быть больше начала.")
        if trim.duration is not None and trim.duration <= 0:
            result.add("INVALID_ARGUMENT", "Длительность фрагмента должна быть положительной.")
        if duration:
            if trim.start is not None and trim.start >= duration:
                result.add("INVALID_ARGUMENT", "Начало обрезки за пределами длительности файла.")
            if trim.end is not None and trim.end > duration + 0.5:
                result.add(
                    "TRIM_RANGE",
                    "Конец обрезки превышает длительность файла — будет использован конец файла.",
                    Severity.WARNING,
                )
        if not trim.accurate and (job.video.mode == "copy"):
            result.add(
                "TRIM_KEYFRAME",
                "Быстрая обрезка выполняется по ключевым кадрам: "
                "фактическая точка начала может отличаться.",
                Severity.INFO,
            )
