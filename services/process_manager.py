"""Запуск процессов FFmpeg вне GUI-потока.

Разделы ТЗ: 25 (Process Manager), 26 (прогресс через `-progress pipe:1`),
29 (shell=False), 30 (atomic output), 33 (логирование), 53 (статистика),
54 (проверка результата).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.error_analyzer import AnalyzedError, ErrorAnalyzer, ErrorCategory
from ..core.models import Job, ProgressInfo, ResultStats
from .ffmpeg_service import creation_flags
from .filesystem_service import FilesystemService

log = logging.getLogger("ffmpeg")

ProgressCallback = Callable[[Job, ProgressInfo], None]
LogCallback = Callable[[Job, str], None]

# Сколько строк stderr держать в памяти для анализа ошибок.
STDERR_TAIL = 200
TERMINATE_TIMEOUT = 5.0


@dataclass
class RunResult:
    """Итог одного запуска FFmpeg."""

    returncode: int
    cancelled: bool = False
    stderr: str = ""
    stats: ResultStats | None = None
    error: AnalyzedError | None = None
    output_path: Path | None = None
    duration: float = 0.0
    log_path: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and self.error is None


def parse_progress_line(line: str, state: dict[str, str]) -> bool:
    """Накапливает пары key=value. True — когда блок завершён (раздел 26)."""
    if "=" not in line:
        return False
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    state[key] = value
    return key == "progress"


def build_progress(state: dict[str, str], total_duration: float | None) -> ProgressInfo:
    """Собирает ProgressInfo из накопленного блока `-progress`."""
    info = ProgressInfo(
        frame=_as_int(state.get("frame")),
        fps=_as_float(state.get("fps")),
        bitrate=state.get("bitrate"),
        total_size=_as_int(state.get("total_size")),
        out_time_us=_as_int(state.get("out_time_us")),
        out_time_ms=_as_int(state.get("out_time_ms")),
        out_time=state.get("out_time"),
        dup_frames=_as_int(state.get("dup_frames")),
        drop_frames=_as_int(state.get("drop_frames")),
        speed=_as_speed(state.get("speed")),
        progress=state.get("progress"),
    )

    if info.out_time_us is not None:
        info.processed_seconds = max(0.0, info.out_time_us / 1_000_000)
    elif info.out_time_ms is not None:
        # FFmpeg исторически отдаёт out_time_ms в микросекундах.
        info.processed_seconds = max(0.0, info.out_time_ms / 1_000_000)
    elif info.out_time:
        info.processed_seconds = _timecode_to_seconds(info.out_time)

    if total_duration and total_duration > 0:
        info.fraction = min(1.0, info.processed_seconds / total_duration)
        remaining = max(0.0, total_duration - info.processed_seconds)
        if info.speed and info.speed > 0:
            info.eta = remaining / info.speed
    if state.get("progress") == "end":
        info.fraction = 1.0
        info.eta = 0.0
    return info


class ProcessManager:
    """Единственное место, где создаются процессы FFmpeg.

    GUI-поток никогда не вызывает эти методы напрямую — только через
    QueueManager/worker (раздел 25).
    """

    def __init__(
        self,
        ffmpeg_service,
        ffprobe_service,
        filesystem: FilesystemService | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.ffmpeg = ffmpeg_service
        self.ffprobe = ffprobe_service
        self.filesystem = filesystem or FilesystemService()
        self.log_dir = Path(log_dir) if log_dir else None
        self.analyzer = ErrorAnalyzer()
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def run_job(
        self,
        job: Job,
        command: list[str],
        total_duration: float | None,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
        final_output: Path | None = None,
        finalize: bool = True,
    ) -> RunResult:
        """Выполняет одну команду, читая прогресс из stdout.

        `command` уже содержит путь к временному файлу (раздел 30);
        `final_output` — куда переименовать результат при успехе.
        `finalize=False` — запуск, после которого файла не остаётся (первый
        проход двухпроходного кодирования): проверять и переименовывать нечего.
        """
        started = time.monotonic()
        temp_output = Path(command[-1])
        destination = Path(final_output) if final_output else job.output_path
        log_file = self._open_log(job, command)

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                shell=False,  # раздел 29
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags(),
            )
        except OSError as exc:
            self.filesystem.discard(temp_output)
            error = AnalyzedError(ErrorCategory.UNKNOWN, "Не удалось запустить FFmpeg.", str(exc))
            self._write_log(log_file, f"SPAWN ERROR: {exc}")
            self._close_log(log_file)
            return RunResult(-1, stderr=str(exc), error=error, log_path=self._log_path(log_file))

        with self._lock:
            self._processes[job.id] = process
            self._cancelled.discard(job.id)

        stderr_lines: list[str] = []
        stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(process, stderr_lines, job, on_log, log_file),
            daemon=True,
        )
        stderr_thread.start()

        state: dict[str, str] = {}
        last_progress = ProgressInfo()
        try:
            if process.stdout is not None:
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    if parse_progress_line(line, state):
                        last_progress = build_progress(state, total_duration)
                        if on_progress:
                            on_progress(job, last_progress)
                        state.clear()
        except (OSError, ValueError) as exc:  # поток закрыт при отмене
            log.debug("Чтение прогресса прервано: %s", exc)

        returncode = process.wait()
        stderr_thread.join(timeout=2)
        with self._lock:
            self._processes.pop(job.id, None)
            cancelled = job.id in self._cancelled
            self._cancelled.discard(job.id)

        elapsed = time.monotonic() - started
        stderr_text = "\n".join(stderr_lines[-STDERR_TAIL:])
        self._write_log(log_file, f"exit code: {returncode}; время: {elapsed:.1f} c")

        if cancelled:
            self.filesystem.discard(temp_output)
            self._write_log(log_file, "Операция отменена пользователем")
            self._close_log(log_file)
            return RunResult(
                returncode,
                cancelled=True,
                stderr=stderr_text,
                duration=elapsed,
                log_path=self._log_path(log_file),
            )

        if returncode != 0:
            self.filesystem.discard(temp_output)
            error = self.analyzer.analyze(stderr_text, returncode)
            self._write_log(log_file, f"Ошибка: {error.category.value}: {error.reason}")
            self._close_log(log_file)
            return RunResult(
                returncode,
                stderr=stderr_text,
                error=error,
                duration=elapsed,
                log_path=self._log_path(log_file),
            )

        if not finalize:
            self._write_log(log_file, "Проход завершён, статистика собрана.")
            self._close_log(log_file)
            return RunResult(
                returncode,
                stderr=stderr_text,
                duration=elapsed,
                log_path=self._log_path(log_file),
            )

        # Раздел 54: результат проверяется FFprobe до переименования.
        verification = self.ffprobe.verify_output(temp_output)
        if not verification.ok:
            self.filesystem.discard(temp_output)
            error = self.analyzer.analyze_validation(verification.message)
            self._write_log(log_file, f"Проверка результата не пройдена: {verification.message}")
            self._close_log(log_file)
            return RunResult(
                returncode,
                stderr=stderr_text,
                error=error,
                duration=elapsed,
                log_path=self._log_path(log_file),
            )

        final_path = self.filesystem.finalize(temp_output, destination)
        stats = self._build_stats(job, final_path, elapsed, last_progress, verification.media)
        self._write_log(
            log_file,
            f"Результат: {final_path} ({stats.output_size} байт), "
            f"экономия {stats.saved_percent:.1f}%",
        )
        self._close_log(log_file)
        return RunResult(
            returncode,
            stderr=stderr_text,
            stats=stats,
            output_path=final_path,
            duration=elapsed,
            log_path=self._log_path(log_file),
        )

    # ------------------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        """terminate() → wait() → при необходимости kill() (раздел 25)."""
        with self._lock:
            process = self._processes.get(job_id)
            if process is None:
                return False
            self._cancelled.add(job_id)

        try:
            process.terminate()
        except OSError as exc:
            log.debug("terminate() не удался: %s", exc)
        try:
            process.wait(timeout=TERMINATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.warning("FFmpeg не завершился, отправляю kill()")
            try:
                process.kill()
                process.wait(timeout=TERMINATE_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.error("Не удалось завершить процесс: %s", exc)
                return False
        return True

    def cancel_all(self) -> None:
        with self._lock:
            job_ids = list(self._processes)
        for job_id in job_ids:
            self.cancel(job_id)

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._processes

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._processes)

    # ------------------------------------------------------------------
    def _drain_stderr(
        self,
        process: subprocess.Popen,
        sink: list[str],
        job: Job,
        on_log: LogCallback | None,
        log_file,
    ) -> None:
        if process.stderr is None:
            return
        try:
            for raw_line in process.stderr:
                line = raw_line.rstrip()
                if not line:
                    continue
                sink.append(line)
                if len(sink) > STDERR_TAIL * 4:
                    del sink[: STDERR_TAIL]
                self._write_log(log_file, line)
                if on_log:
                    on_log(job, line)
        except (OSError, ValueError):
            pass

    def _build_stats(
        self,
        job: Job,
        output_path: Path,
        elapsed: float,
        progress: ProgressInfo,
        media,
    ) -> ResultStats:
        input_size = job.source.size if job.source else _size_of(job.input_path)
        output_size = _size_of(output_path)
        source_codec = (
            job.source.primary_video.codec_name
            if job.source and job.source.primary_video
            else ""
        )
        result_codec = media.primary_video.codec_name if media and media.primary_video else ""
        codec_text = (
            f"{source_codec.upper()} → {result_codec.upper()}"
            if source_codec and result_codec
            else (result_codec or source_codec).upper()
        )
        audio_codec = ""
        if media and media.primary_audio:
            audio = media.primary_audio
            audio_codec = audio.codec_name.upper()
            if audio.bit_rate:
                audio_codec += f" {audio.bit_rate // 1000}k"

        average_speed = progress.speed
        if average_speed is None and job.source and job.source.duration and elapsed > 0:
            average_speed = job.source.duration / elapsed

        return ResultStats(
            input_size=input_size,
            output_size=output_size,
            processing_time=elapsed,
            average_speed=average_speed,
            codec=codec_text,
            encoder=job.video_encoder or job.video.encoder or "",
            audio_codec=audio_codec,
        )

    # -- логи (раздел 33) --------------------------------------------------
    def _open_log(self, job: Job, command: list[str]):
        if not self.log_dir:
            return None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / f"{job.id}.log"
            handle = path.open("a", encoding="utf-8")
        except OSError as exc:
            log.warning("Не удалось открыть лог задания: %s", exc)
            return None
        job.log_path = str(path)
        version = self.ffmpeg.binaries.ffmpeg_version if self.ffmpeg else ""
        handle.write(f"=== {job.label} | {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        handle.write(f"FFmpeg: {version}\n")
        handle.write("Команда: " + " ".join(command) + "\n")
        handle.flush()
        return handle

    @staticmethod
    def _write_log(handle, message: str) -> None:
        if handle is None:
            return
        try:
            handle.write(message + "\n")
            handle.flush()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _close_log(handle) -> None:
        if handle is None:
            return
        try:
            handle.write("=== конец ===\n")
            handle.close()
        except (OSError, ValueError):
            pass

    @staticmethod
    def _log_path(handle) -> Path | None:
        if handle is None:
            return None
        try:
            return Path(handle.name)
        except (AttributeError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Разбор значений
# ---------------------------------------------------------------------------


def _as_int(value: str | None) -> int | None:
    if value is None or value in ("N/A", ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: str | None) -> float | None:
    if value is None or value in ("N/A", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_speed(value: str | None) -> float | None:
    """'2.14x' → 2.14."""
    if not value or value in ("N/A", ""):
        return None
    return _as_float(value.rstrip("xX").strip())


def _timecode_to_seconds(value: str) -> float:
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return 0.0


def _size_of(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0
