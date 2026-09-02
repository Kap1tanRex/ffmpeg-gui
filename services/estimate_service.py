"""Оценка итогового размера файла.

Два пути, в зависимости от задания:

* потоки копируются — считать нечего: байты те же, что в исходнике, меняется
  только длительность. Ответ мгновенный, без запуска FFmpeg;
* идёт перекодирование — кодируется короткий фрагмент из середины будущего
  результата, и его размер экстраполируется на полную длительность.

Оценка нужна не столько ради точной цифры, сколько чтобы сразу было видно,
не окажется ли результат неожиданно больше исходника.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..core.command_builder import CommandBuildError, CommandBuilder
from ..core.models import Job, TrimOptions
from .ffmpeg_service import FFmpegService, creation_flags
from .ffprobe_service import FFprobeService
from .filesystem_service import FilesystemService

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 6.0
SAMPLE_TIMEOUT = 60


@dataclass
class SizeEstimate:
    ok: bool
    estimated_bytes: int = 0
    sample_bytes: int = 0
    sample_seconds: float = 0.0
    message: str = ""


class EstimateService:
    """Оценка размера результата.

    Вызывается интерфейсом автоматически при изменении параметров, поэтому
    пробный фрагмент короткий, а вызывающая сторона отвечает за паузу между
    пересчётами (см. ``gui/tabs/_estimate.py``).
    """

    def __init__(
        self,
        ffmpeg: FFmpegService,
        filesystem: FilesystemService,
        builder: CommandBuilder,
        probe: FFprobeService | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.filesystem = filesystem
        self.builder = builder
        self.probe = probe
        # Пробное кодирование запускается автоматически и часто, поэтому его
        # нужно уметь прерывать: при закрытии приложения и когда параметры
        # сменились до того, как предыдущая оценка досчиталась.
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def estimate(self, job: Job) -> SizeEstimate:
        if not self.ffmpeg.available:
            return SizeEstimate(False, message="FFmpeg не найден.")

        source_duration = job.source.duration if job.source else 0.0
        if not source_duration or source_duration <= 0:
            return SizeEstimate(False, message="Неизвестна длительность исходного файла.")

        # При обрезке результат длится не столько, сколько исходник: и сэмпл
        # берётся изнутри выбранного диапазона, и множитель считается по нему,
        # иначе оценка получалась кратно завышенной.
        window_start = 0.0
        target_duration = source_duration
        if job.trim.enabled:
            window_start = max(0.0, job.trim.start or 0.0)
            trimmed = job.trim.effective_duration()
            if trimmed is None:
                trimmed = max(0.0, source_duration - window_start)
            target_duration = min(trimmed, max(0.0, source_duration - window_start))

        if target_duration <= 0:
            return SizeEstimate(False, message="Пустой диапазон обрезки.")

        # Копирование потоков не требует пробного кодирования: байты те же, что
        # в исходнике, меняется только длительность. Заодно это единственный
        # надёжный способ: при копировании перемотка идёт до ближайшего
        # ключевого кадра, и пробный фрагмент содержит больше данных, чем
        # заявляет его длительность — оценка по нему завышалась в разы.
        source_size = job.source.size if job.source else 0
        if job.video.mode == "copy" and job.audio.mode == "copy" and source_size:
            share = target_duration / source_duration
            return SizeEstimate(
                True,
                estimated_bytes=int(source_size * share),
                sample_bytes=source_size,
                sample_seconds=source_duration,
            )

        sample_seconds = min(SAMPLE_SECONDS, target_duration)
        start = window_start + max(0.0, (target_duration - sample_seconds) / 2)

        sample_job = Job(
            input_files=list(job.input_files),
            output_file=job.output_file,
            operation=job.operation,
            container=job.container,
            video_encoder=job.video_encoder,
            audio_encoder=job.audio_encoder,
            video=job.video,
            audio=job.audio,
            subtitles=job.subtitles,
            source=job.source,
            trim=TrimOptions(enabled=True, start=start, duration=sample_seconds, accurate=True),
        )

        extension = Path(job.output_file).suffix or ".tmp"
        temp_output = self.filesystem.jobs_dir / f"estimate_{uuid.uuid4().hex}{extension}"
        try:
            command = self.builder.build(sample_job, output_override=temp_output, for_execution=True)
        except CommandBuildError as exc:
            return SizeEstimate(False, message=str(exc))

        returncode = self._run(command)
        if returncode != 0 or not temp_output.exists():
            self.filesystem.discard(temp_output)
            return SizeEstimate(False, message="Пробное кодирование завершилось с ошибкой.")

        sample_bytes = temp_output.stat().st_size
        # Сколько секунд получилось на самом деле — а не сколько просили.
        # При копировании потоков перемотка идёт до ближайшего ключевого кадра,
        # и фрагмент выходит длиннее заказанного; множитель по заказанной
        # длительности завышал бы оценку в разы.
        measured = self._measure(temp_output) or sample_seconds
        self.filesystem.discard(temp_output)

        if sample_bytes <= 0:
            return SizeEstimate(False, message="Пробный фрагмент получился пустым.")

        ratio = target_duration / measured
        estimated_bytes = int(sample_bytes * ratio)
        return SizeEstimate(
            True,
            estimated_bytes=estimated_bytes,
            sample_bytes=sample_bytes,
            sample_seconds=measured,
        )

    # -- запуск пробного кодирования ------------------------------------------
    def _run(self, command: list[str]) -> int:
        """Запускает пробное кодирование, давая себя прервать.

        Вывод уходит в никуда: он не нужен, а канал пришлось бы вычитывать,
        иначе FFmpeg с ``-progress`` рано или поздно упрётся в полный буфер.
        """
        try:
            process = subprocess.Popen(  # noqa: S603 - список аргументов, shell=False
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags(),
            )
        except OSError as exc:
            log.warning("Оценка размера не удалась: %s", exc)
            return -1

        with self._lock:
            self._process = process
        try:
            return process.wait(timeout=SAMPLE_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.info("Пробное кодирование не уложилось в %s с", SAMPLE_TIMEOUT)
            process.kill()
            process.wait()
            return -1
        finally:
            with self._lock:
                self._process = None

    def cancel(self) -> None:
        """Прерывает идущее пробное кодирование. Безопасно вызывать всегда."""
        with self._lock:
            process = self._process
        if process is None:
            return
        try:
            process.terminate()
        except OSError as exc:  # pragma: no cover - процесс уже завершился
            log.debug("не удалось прервать пробное кодирование: %s", exc)

    def _measure(self, path: Path) -> float | None:
        """Фактическая длительность пробного фрагмента по данным FFprobe."""
        if self.probe is None:
            return None
        try:
            media = self.probe.probe(path)
        except Exception:  # noqa: BLE001 - оценка не должна падать из-за разбора
            log.debug("не удалось измерить пробный фрагмент", exc_info=True)
            return None
        duration = media.duration or 0.0
        return duration if duration > 0 else None
