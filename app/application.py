"""Application Controller.

Связывает GUI со слоями модели, capabilities, валидации и очереди
(разделы 2 и 63). GUI обращается только к этому классу, а не к сервисам
напрямую.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import threading
from collections.abc import Iterable
from pathlib import Path

from ..core.capabilities import CapabilityManager, FFmpegCapabilities
from ..core.command_builder import CommandBuilder, CommandBuildError, command_to_string
from ..core.compatibility import CompatibilityService
from ..core.error_analyzer import ErrorAnalyzer
from ..core.help_registry import HelpRegistry, registry as help_registry
from ..core.models import (
    AudioOptions,
    Job,
    MediaFile,
    Operation,
    SubtitleOptions,
    TrimOptions,
    VideoOptions,
)
from ..core.profiles import ProfileManager
from ..core.validator import ValidationResult, Validator
from ..services.estimate_service import EstimateService
from ..services.ffmpeg_service import FFmpegService, SystemInfo
from ..services.ffprobe_service import FFprobeService
from ..services.filesystem_service import FilesystemService, ScanOptions
from ..services.gpu_detector import GpuInfo, detect_gpus, hardware_suffixes_for_vendor
from ..services.ffmpeg_updater import (
    FFmpegUpdater,
    FFmpegUpdateError,
    FFmpegUpdateState,
    app_ffmpeg_dir,
)
from ..services.update_service import UpdateService, UpdateState
from ..services.preview_service import PreviewService
from ..services.process_manager import ProcessManager
from ..services.queue_manager import QueueCallbacks, QueueManager
from .events import Event, EventBus
from .state import APP_VERSION, AppState, SettingsStore

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = PACKAGE_ROOT / "resources" / "translations"


class Translator:
    """Локализация интерфейса (раздел 55)."""

    def __init__(self, language: str = "ru") -> None:
        self.language = language
        self._strings: dict[str, str] = {}
        self.load(language)

    def load(self, language: str) -> None:
        self.language = language
        path = TRANSLATIONS_DIR / f"{language}.json"
        try:
            self._strings = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Перевод %s не загружен: %s", language, exc)
            self._strings = {}

    def __call__(self, key: str, default: str | None = None) -> str:
        return self._strings.get(key, default if default is not None else key)

    @staticmethod
    def available_languages() -> list[str]:
        if not TRANSLATIONS_DIR.is_dir():
            return ["ru"]
        return sorted(path.stem for path in TRANSLATIONS_DIR.glob("*.json"))


class Application:
    """Фасад приложения."""

    def __init__(self, settings_dir: Path | None = None) -> None:
        self.bus = EventBus()
        self.store = SettingsStore(settings_dir)
        self.settings = self.store.load()
        self.state = AppState(settings=self.settings)
        self.translator = Translator(self.settings.language)

        self._setup_logging()

        self.ffmpeg = FFmpegService(self.settings.ffmpeg_path, self.settings.ffprobe_path)
        self.ffprobe = FFprobeService(self.ffmpeg)
        self.filesystem = FilesystemService()
        self.capability_manager = CapabilityManager(self.ffmpeg, self.store.cache_path)
        self.capabilities = FFmpegCapabilities()
        self.compat = CompatibilityService(self.capabilities)
        self.builder = CommandBuilder(self.capabilities)
        self.validator = Validator(self.capabilities)
        self.analyzer = ErrorAnalyzer()
        self.help: HelpRegistry = help_registry
        self.profiles = ProfileManager(self.store.profiles_dir)
        self.preview = PreviewService(self.ffmpeg, self.filesystem)
        self.estimate_service = EstimateService(
            self.ffmpeg, self.filesystem, self.builder, probe=self.ffprobe
        )

        self.processes = ProcessManager(
            self.ffmpeg, self.ffprobe, self.filesystem, self.store.logs_dir
        )
        self.queue = QueueManager(
            self.processes,
            self.builder,
            self.validator,
            self.filesystem,
            QueueCallbacks(
                on_job_added=lambda job: self.bus.publish(Event.JOB_ADDED, job),
                on_job_started=lambda job: self.bus.publish(Event.JOB_STARTED, job),
                on_job_progress=lambda job, info: self.bus.publish(Event.JOB_PROGRESS, job, info),
                on_job_finished=self._on_job_finished,
                on_job_log=lambda job, line: self.bus.publish(Event.JOB_LOG, job, line),
                on_queue_changed=lambda: self.bus.publish(Event.QUEUE_CHANGED),
                on_queue_idle=lambda: self.bus.publish(Event.QUEUE_IDLE),
            ),
            parallel_jobs=self.settings.parallel_jobs,
        )
        self._recent_errors: list[str] = []
        self.gpu_info: list[GpuInfo] = []
        self.updates = UpdateService(self.settings.update_repository)
        self.update_state = UpdateState()
        self.ffmpeg_updates = FFmpegUpdater(self.settings.ffmpeg_source)
        self.ffmpeg_update_state = FFmpegUpdateState()

    # -- инициализация -----------------------------------------------------
    def _setup_logging(self) -> None:
        """Раздел 33: logs/app.log и logs/ffmpeg.log."""
        logs_dir = self.store.logs_dir
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        root = logging.getLogger()
        if any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in root.handlers):
            return
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

        app_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        app_handler.setFormatter(formatter)
        root.addHandler(app_handler)
        root.setLevel(logging.INFO)

        ffmpeg_handler = logging.handlers.RotatingFileHandler(
            logs_dir / "ffmpeg.log", maxBytes=4 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        ffmpeg_handler.setFormatter(formatter)
        ffmpeg_logger = logging.getLogger("ffmpeg")
        ffmpeg_logger.addHandler(ffmpeg_handler)
        ffmpeg_logger.propagate = False

    def initialize(self, async_detect: bool = True) -> None:
        """Проверяет FFmpeg и запускает обнаружение возможностей и GPU."""
        self.filesystem.cleanup_temp()
        if self.settings.check_updates_on_start:
            self.check_updates(async_check=async_detect, silent=True)
        if async_detect:
            threading.Thread(target=self._detect_gpu, daemon=True).start()
        else:
            self._detect_gpu()
        if not self.ffmpeg.available:
            self.bus.publish(Event.FFMPEG_MISSING)
            return
        if async_detect:
            threading.Thread(target=self._detect_capabilities, daemon=True).start()
        else:
            self._detect_capabilities()

    def _detect_capabilities(self) -> None:
        capabilities = self.capability_manager.detect()
        self.apply_capabilities(capabilities)
        self.bus.publish(Event.CAPABILITIES_READY, capabilities)

    def _detect_gpu(self) -> None:
        """Раздел 35: определение видеокарты для автовыбора аппаратного энкодера."""
        self.gpu_info = detect_gpus()
        self.bus.publish(Event.GPU_DETECTED, self.gpu_info)

    # -- обновления ----------------------------------------------------------
    def check_updates(self, async_check: bool = True, silent: bool = False) -> None:
        """Спрашивает у репозитория выпуски; ответ приходит событием.

        Сеть — единственная часть программы, которая может ждать минуту, и
        держать из-за неё интерфейс нельзя. ``silent`` — для проверки при
        запуске: пока репозиторий не задан, ходить некуда и жаловаться не на
        что, а по кнопке в настройках причина показывается прямо.
        """
        self.updates.repository = self.settings.update_repository
        if silent and not self.settings.update_repository.strip():
            return
        if async_check:
            threading.Thread(target=self._check_updates, daemon=True).start()
        else:
            self._check_updates()

    def _check_updates(self) -> None:
        self.update_state = self.updates.check(
            APP_VERSION, prerelease=self.settings.update_prereleases
        )
        self.bus.publish(Event.UPDATE_CHECKED, self.update_state)

    # -- сборка FFmpeg ---------------------------------------------------------
    def check_ffmpeg_update(self, async_check: bool = True) -> None:
        """Спрашивает у выбранного источника, какая сборка FFmpeg доступна."""
        self.ffmpeg_updates.source = self.settings.ffmpeg_source
        if async_check:
            threading.Thread(target=self._check_ffmpeg_update, daemon=True).start()
        else:
            self._check_ffmpeg_update()

    def _check_ffmpeg_update(self) -> None:
        self.ffmpeg_update_state = self.ffmpeg_updates.check(
            self.ffmpeg.binaries.ffmpeg_version
        )
        self.bus.publish(Event.FFMPEG_UPDATE_CHECKED, self.ffmpeg_update_state)

    def install_ffmpeg_update(self, async_install: bool = True) -> None:
        """Скачивает и раскладывает сборку в корень приложения.

        Путь к FFmpeg после установки прописывается в настройки: пользователь
        выбрал конкретную сборку, и она должна использоваться, даже если в
        системе есть другая.
        """
        build = self.ffmpeg_update_state.build
        if build is None:
            return
        if async_install:
            threading.Thread(target=self._install_ffmpeg, args=(build,), daemon=True).start()
        else:
            self._install_ffmpeg(build)

    def _install_ffmpeg(self, build) -> None:
        def report(received: int, total: int) -> None:
            self.bus.publish(Event.FFMPEG_UPDATE_PROGRESS, received, total)

        try:
            binary = self.ffmpeg_updates.install(build, self.app_root, progress=report)
        except FFmpegUpdateError as exc:
            self.bus.publish(Event.FFMPEG_UPDATE_FINISHED, False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - фоновый поток не должен падать молча
            log.exception("установка сборки FFmpeg не удалась")
            self.bus.publish(Event.FFMPEG_UPDATE_FINISHED, False, str(exc))
            return

        self.set_ffmpeg_paths(str(binary), str(binary.with_name("ffprobe.exe")))
        self._check_ffmpeg_update()
        self.bus.publish(
            Event.FFMPEG_UPDATE_FINISHED, True, self.ffmpeg.binaries.ffmpeg_version
        )

    @property
    def app_root(self) -> Path:
        """Корень приложения: рядом с .exe в сборке, каталог пакета в исходниках."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def redetect_gpu(self) -> list[GpuInfo]:
        """Повторное определение GPU по кнопке в настройках."""
        self._detect_gpu()
        return self.gpu_info

    def available_hw_suffixes(self) -> tuple[str, ...]:
        """Раздел 35: суффиксы энкодеров, под которые в системе есть видеокарта.

        Пустой кортеж означает «определить не удалось» (видеокарты не найдены
        или производитель неизвестен), а не «аппаратного кодирования нет»:
        по нему ничего не скрывается, иначе на нераспознанном железе пропали
        бы рабочие варианты.
        """
        suffixes: list[str] = []
        for gpu in self.gpu_info:
            for suffix in hardware_suffixes_for_vendor(gpu.vendor):
                if suffix not in suffixes:
                    suffixes.append(suffix)
        return tuple(suffixes)

    def preferred_hw_suffixes(self) -> tuple[str, ...]:
        """То же, но с оглядкой на настройку автовыбора: пустой кортеж, когда
        автовыбор выключен, — тогда берётся обычный (программный) энкодер."""
        if not self.settings.auto_hardware_encoding:
            return ()
        return self.available_hw_suffixes()

    def apply_capabilities(self, capabilities: FFmpegCapabilities) -> None:
        self.capabilities = capabilities
        self.compat = CompatibilityService(capabilities)
        self.builder = CommandBuilder(capabilities, str(self.ffmpeg.binaries.ffmpeg or "ffmpeg"))
        self.validator = Validator(capabilities)
        self.queue.builder = self.builder
        self.queue.validator = self.validator
        self.estimate_service.builder = self.builder
        # Передаём билдеру реальные списки опций энкодеров (раздел 21).
        for name in ("libx264", "libx265", "libsvtav1", "libvpx-vp9"):
            if capabilities.has_encoder(name):
                options = {option.name for option in self.capability_manager.encoder_options(name)}
                self.builder.set_encoder_options(name, options)

    def set_ffmpeg_paths(self, ffmpeg_path: str, ffprobe_path: str) -> bool:
        """Ручное указание путей (раздел 46)."""
        self.ffmpeg.locate(ffmpeg_path, ffprobe_path)
        if not self.ffmpeg.available:
            self.bus.publish(Event.FFMPEG_MISSING)
            return False
        self.settings.ffmpeg_path = str(self.ffmpeg.binaries.ffmpeg)
        self.settings.ffprobe_path = str(self.ffmpeg.binaries.ffprobe)
        self.store.save(self.settings)
        self.capability_manager = CapabilityManager(self.ffmpeg, self.store.cache_path)
        self.redetect_capabilities()
        return True

    def redetect_capabilities(self) -> None:
        """Раздел 46: принудительное обновление capabilities (кнопка настроек)."""
        threading.Thread(target=self._detect_capabilities, daemon=True).start()

    # -- импорт (раздел 6) --------------------------------------------------
    def import_paths(self, paths: Iterable[str | Path], async_probe: bool = True) -> None:
        """Импорт файлов и папок; каждый файл проверяется FFprobe."""
        collected: list[Path] = []
        scan_options = ScanOptions(
            recursive=self.settings.recursive_import,
            video=self.settings.import_video,
            audio=self.settings.import_audio,
            images=self.settings.import_images,
        )
        for raw in paths:
            path = Path(str(raw).strip().strip('"').strip("{}"))
            if path.is_dir():
                collected.extend(self.filesystem.scan_folder(path, scan_options))
            elif path.is_file():
                collected.append(path)

        if not collected:
            self.bus.publish(Event.IMPORT_FINISHED, 0, 0)
            return

        self.bus.publish(Event.IMPORT_STARTED, len(collected))
        if async_probe:
            threading.Thread(target=self._probe_all, args=(collected,), daemon=True).start()
        else:
            self._probe_all(collected)

    def _probe_all(self, paths: list[Path]) -> None:
        added = 0
        failed = 0
        for path in paths:
            media = (
                self.ffprobe.probe(path)
                if self.settings.auto_probe
                else MediaFile(path=path, size=path.stat().st_size)
            )
            if media.probe_error and not media.streams:
                failed += 1
                # Расширение не является критерием: файл без потоков не медиа.
                log.info("Пропущен файл %s: %s", path.name, media.probe_error)
                continue
            if self.state.add_file(media):
                added += 1
        self.bus.publish(Event.FILES_CHANGED, self.state.files)
        self.bus.publish(Event.IMPORT_FINISHED, added, failed)

    def remove_file(self, index: int) -> None:
        if self.state.remove_at(index):
            self.bus.publish(Event.FILES_CHANGED, self.state.files)

    def clear_files(self) -> None:
        self.state.clear()
        self.bus.publish(Event.FILES_CHANGED, self.state.files)

    def select_file(self, index: int) -> MediaFile | None:
        media = self.state.select(index)
        self.bus.publish(Event.FILE_SELECTED, media)
        return media

    def reprobe(self, media: MediaFile) -> MediaFile:
        fresh = self.ffprobe.probe(media.path)
        for index, item in enumerate(self.state.files):
            if item.path == media.path:
                self.state.files[index] = fresh
                break
        self.bus.publish(Event.FILES_CHANGED, self.state.files)
        return fresh

    # -- построение заданий -------------------------------------------------
    def build_job(
        self,
        media: MediaFile,
        operation: str,
        container: str | None = None,
        video: VideoOptions | None = None,
        audio: AudioOptions | None = None,
        trim: TrimOptions | None = None,
        subtitles: SubtitleOptions | None = None,
        output_path: str | Path | None = None,
        stream_map: list[str] | None = None,
        suffix: str | None = None,
    ) -> Job:
        """Собирает Job из выбора пользователя, разрешая «Авто»."""
        video = video or VideoOptions()
        audio = audio or AudioOptions()
        trim = trim or TrimOptions()
        subtitles = subtitles or SubtitleOptions()

        if container is None:
            container = self.compat.muxer_for_extension(media.path.suffix) or "matroska"

        if output_path is None:
            extension = self.compat.extension_for_muxer(container)
            default_suffix = suffix if suffix is not None else self._default_suffix(operation)
            output_path = self.filesystem.suggest_output(
                media.path, self.settings.output_directory or None, extension, default_suffix
            )

        job = Job(
            input_files=[str(media.path)],
            output_file=str(output_path),
            operation=operation,
            container=container,
            video=video,
            audio=audio,
            trim=trim,
            subtitles=subtitles,
            source=media,
            overwrite_policy=self.settings.overwrite_policy,
            stream_map=stream_map or [],
        )

        if video.mode == "encode":
            job.video_encoder = video.encoder or self.compat.resolve_encoder(
                video.codec, "video", self.preferred_hw_suffixes()
            )
        if audio.mode == "encode":
            job.audio_encoder = audio.encoder or self.compat.resolve_encoder(audio.codec, "audio")

        if not media.has_video and container and not self.compat.is_audio_only(container):
            job.video.mode = "none"

        return job

    @staticmethod
    def _default_suffix(operation: str) -> str:
        return {
            Operation.COMPRESS.value: "_compressed",
            Operation.CONVERT.value: "_converted",
            Operation.TRIM.value: "_trimmed",
        }.get(operation, "_out")

    def apply_profile(self, job: Job, profile_name: str) -> Job:
        profile = self.profiles.get(profile_name)
        if profile is None:
            return job
        profile.apply(job)
        if job.video.mode == "encode":
            job.video_encoder = job.video.encoder or self.compat.resolve_encoder(
                job.video.codec, "video", self.preferred_hw_suffixes()
            )
        if job.audio.mode == "encode":
            job.audio_encoder = job.audio.encoder or self.compat.resolve_encoder(
                job.audio.codec, "audio"
            )
        self.settings.last_profile = profile.name
        return job

    def suggest_stream_copy(self, media: MediaFile, container: str, accurate_trim: bool = False):
        """Раздел 15."""
        return self.compat.analyze_copy(media, container, accurate_trim)

    def validate(self, job: Job) -> ValidationResult:
        return self.validator.validate(job)

    def preview_command(self, job: Job) -> str:
        """Строка команды для окна «Показать команду FFmpeg» (раздел 48)."""
        try:
            command = self.builder.build_preview(job)
        except CommandBuildError as exc:
            return f"# {exc}"
        job.command = command
        text = command_to_string(command)
        self.bus.publish(Event.COMMAND_CHANGED, text)
        return text

    # -- очередь ------------------------------------------------------------
    def enqueue(self, job: Job, autostart: bool = True) -> Job:
        self.queue.add(job)
        if autostart:
            self.queue.start()
        return job

    def enqueue_many(self, jobs: list[Job], autostart: bool = True) -> None:
        for job in jobs:
            self.queue.add(job)
        if autostart and jobs:
            self.queue.start()

    def _on_job_finished(self, job: Job) -> None:
        if job.error:
            self._recent_errors.append(f"{job.label}: {job.error.splitlines()[0]}")
            del self._recent_errors[:-10]
        self.bus.publish(Event.JOB_FINISHED, job)

    # -- настройки ----------------------------------------------------------
    def update_settings(self, **changes) -> None:
        for key, value in changes.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.store.save(self.settings)
        self.queue.set_parallel_jobs(self.settings.parallel_jobs)
        if self.translator.language != self.settings.language:
            self.translator.load(self.settings.language)
        self.bus.publish(Event.SETTINGS_CHANGED, self.settings)

    # -- диагностика (раздел 47) ---------------------------------------------
    def diagnostics(self) -> SystemInfo:
        info = SystemInfo(
            app_version=APP_VERSION,
            ffmpeg_version=self.ffmpeg.binaries.ffmpeg_version,
            ffprobe_version=self.ffmpeg.binaries.ffprobe_version,
            ffmpeg_path=str(self.ffmpeg.binaries.ffmpeg or ""),
            build_configuration=self.capabilities.build_configuration,
            encoders_count=len(self.capabilities.encoders),
            hwaccels=list(self.capabilities.hwaccels),
            free_disk_space=self.filesystem.free_space(
                self.settings.output_directory or Path.home()
            ),
            recent_errors=list(self._recent_errors),
        )
        return info

    def export_diagnostics(self, path: str | Path) -> Path:
        target = Path(path)
        target.write_text(self.diagnostics().to_text(), encoding="utf-8")
        return target

    # -- завершение ----------------------------------------------------------
    def shutdown(self) -> None:
        self.queue.shutdown()
        self.estimate_service.cancel()
        self.updates.cancel()
        self.ffmpeg_updates.cancel()
        self.store.save(self.settings)
        self.filesystem.cleanup_temp()
