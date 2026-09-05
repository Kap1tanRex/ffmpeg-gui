"""Очередь заданий с параллельным выполнением (раздел 24).

`QueueManager` — единственное место, порождающее рабочие потоки для
`ProcessManager`. GUI никогда не запускает FFmpeg напрямую: он добавляет
`Job` в очередь и подписывается на события через колбэки/шину событий.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..core.command_builder import CommandBuildError, CommandBuilder
from ..core.models import Job, JobStatus, OverwritePolicy, ProgressInfo
from ..core.validator import Validator
from .filesystem_service import FilesystemService
from .process_manager import ProcessManager

log = logging.getLogger(__name__)


@dataclass
class QueueCallbacks:
    on_job_added: Callable[[Job], None] | None = None
    on_job_started: Callable[[Job], None] | None = None
    on_job_progress: Callable[[Job, ProgressInfo], None] | None = None
    on_job_finished: Callable[[Job], None] | None = None
    on_job_log: Callable[[Job, str], None] | None = None
    on_queue_changed: Callable[[], None] | None = None
    on_queue_idle: Callable[[], None] | None = None

    def _call(self, name: str, *args) -> None:
        callback = getattr(self, name)
        if callback is not None:
            try:
                callback(*args)
            except Exception:  # noqa: BLE001 - колбэки не должны ронять очередь
                log.exception("Ошибка в колбэке очереди %s", name)


class QueueManager:
    """Раздел 24: несколько заданий, параллелизм, пауза, отмена, порядок."""

    def __init__(
        self,
        processes: ProcessManager,
        builder: CommandBuilder,
        validator: Validator,
        filesystem: FilesystemService,
        callbacks: QueueCallbacks | None = None,
        parallel_jobs: int = 1,
    ) -> None:
        self.processes = processes
        self.builder = builder
        self.validator = validator
        self.filesystem = filesystem
        self.callbacks = callbacks or QueueCallbacks()
        self.parallel_jobs = max(1, parallel_jobs)

        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._running: set[str] = set()
        self._active = False

    # -- управление ---------------------------------------------------------
    def add(self, job: Job) -> None:
        with self._lock:
            self.jobs.append(job)
        self.callbacks._call("on_job_added", job)
        self.callbacks._call("on_queue_changed")

    def start(self) -> None:
        self._active = True
        self._dispatch()

    def set_parallel_jobs(self, count: int) -> None:
        self.parallel_jobs = max(1, count)
        self._dispatch()

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._find(job_id)
            if job is None or job.is_active:
                return False
            self.jobs.remove(job)
        self.callbacks._call("on_queue_changed")
        return True

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [job for job in self.jobs if not job.is_finished]
        self.callbacks._call("on_queue_changed")

    def move(self, job_id: str, offset: int) -> bool:
        """Раздел 24: перестановка задания в очереди (offset: -1 вверх, +1 вниз)."""
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return False
            index = self.jobs.index(job)
            new_index = max(0, min(len(self.jobs) - 1, index + offset))
            if new_index == index:
                return False
            self.jobs.pop(index)
            self.jobs.insert(new_index, job)
        self.callbacks._call("on_queue_changed")
        return True

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return False
            if job.status == JobStatus.PENDING.value:
                job.status = JobStatus.CANCELLED.value
                self.callbacks._call("on_queue_changed")
                return True
        return self.processes.cancel(job_id)

    def shutdown(self) -> None:
        self._active = False
        self.processes.cancel_all()

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._running)

    # -- внутренняя механика --------------------------------------------------
    def _find(self, job_id: str) -> Job | None:
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def _dispatch(self) -> None:
        if not self._active:
            return
        with self._lock:
            while len(self._running) < self.parallel_jobs:
                next_job = next(
                    (j for j in self.jobs if j.status == JobStatus.PENDING.value), None
                )
                if next_job is None:
                    break
                next_job.status = JobStatus.RUNNING.value
                self._running.add(next_job.id)
                thread = threading.Thread(target=self._run_job, args=(next_job,), daemon=True)
                thread.start()

    def _run_job(self, job: Job) -> None:
        job.started_at = datetime.now()
        self.callbacks._call("on_job_started", job)

        validation = self.validator.validate(job)
        if not validation.ok:
            job.status = JobStatus.FAILED.value
            job.error = "\n".join(issue.message for issue in validation.errors)
            job.error_category = "invalid_argument"
            self._finish(job)
            return

        destination = job.output_path
        if job.overwrite_policy == OverwritePolicy.RENAME.value and destination.exists():
            destination = self.filesystem.unique_path(destination)
        temp_output = self.filesystem.temp_output_for(destination)

        total_duration = job.effective_duration()

        passlog = str(temp_output) + ".pass"
        retried = False
        while True:
            passes = 2 if self._two_pass(job) else 1
            try:
                commands = [
                    self.builder.build(
                        job,
                        output_override=temp_output,
                        for_execution=True,
                        pass_number=(number if passes > 1 else None),
                        passlog=(passlog if passes > 1 else None),
                    )
                    for number in range(1, passes + 1)
                ]
            except CommandBuildError as exc:
                job.status = JobStatus.FAILED.value
                job.error = str(exc)
                job.error_category = "invalid_argument"
                self._finish(job)
                return

            for index, command in enumerate(commands):
                job.command = command
                if passes > 1:
                    self.callbacks._call(
                        "on_job_log", job, f"Проход {index + 1} из {passes}."
                    )
                result = self.processes.run_job(
                    job,
                    command,
                    total_duration,
                    on_progress=lambda j, info, i=index: self._progress(j, info, i, passes),
                    on_log=lambda j, line: self.callbacks._call("on_job_log", j, line),
                    final_output=destination,
                    finalize=index == len(commands) - 1,
                )
                if not result.ok:
                    break

            if retried or not self._drop_hardware(job, result):
                break
            retried = True
            job.progress = 0.0
            self.callbacks._call(
                "on_job_log",
                job,
                "Аппаратное кодирование не завелось — повтор на процессоре.",
            )

        self._discard_passlog(passlog)

        if result.cancelled:
            job.status = JobStatus.CANCELLED.value
        elif result.ok:
            job.status = JobStatus.COMPLETED.value
            job.progress = 1.0
            job.stats = result.stats
            job.output_file = str(result.output_path or destination)
        else:
            job.status = JobStatus.FAILED.value
            job.error = result.error.report() if result.error else result.stderr
            job.error_category = result.error.category.value if result.error else None

        job.log_path = str(result.log_path) if result.log_path else job.log_path
        self._finish(job)

    @staticmethod
    def _two_pass(job: Job) -> bool:
        """Два прохода имеют смысл только там, где задан битрейт: при
        постоянном качестве (CRF) кодировщику нечего распределять."""
        return (
            job.video.two_pass
            and job.video.mode == "encode"
            and job.video.quality_mode in ("bitrate", "size")
        )

    def _progress(self, job: Job, info, index: int, passes: int) -> None:
        """Растягивает прогресс прохода на его долю от общего."""
        if passes > 1:
            info.fraction = (index + info.fraction) / passes
        self.callbacks._call("on_job_progress", job, info)

    @staticmethod
    def _discard_passlog(passlog: str) -> None:
        """FFmpeg оставляет рядом с выходом файлы статистики (-0.log,
        -0.log.mbtree) — они нужны только между проходами."""
        base = Path(passlog)
        for leftover in base.parent.glob(base.name + "*"):
            try:
                leftover.unlink()
            except OSError as exc:  # noqa: PERF203 - чужой файл не должен ронять задание
                log.debug("Не удалось убрать %s: %s", leftover, exc)

    def _drop_hardware(self, job: Job, result) -> bool:
        """Снимает с задания аппаратный энкодер после отказа драйвера.

        Пользователю нужен готовый файл, а не сообщение про NVENC: если
        видеокарта не отозвалась, то же задание повторяется на процессоре.
        Энкодер очищается в обоих полях, откуда его берёт CommandBuilder, —
        дальше CompatibilityService сам выберет программный вариант.
        Возвращает False, когда очищать нечего: команда не изменилась бы.
        """
        if result.ok or result.cancelled or result.error is None:
            return False
        if result.error.category.value != "hwaccel_error":
            return False
        if not (job.video_encoder or job.video.encoder or job.hwaccel):
            return False
        job.video_encoder = None
        job.video.encoder = None
        job.hwaccel = None
        return True

    def _finish(self, job: Job) -> None:
        job.finished_at = datetime.now()
        with self._lock:
            self._running.discard(job.id)
        self.callbacks._call("on_job_finished", job)
        self.callbacks._call("on_queue_changed")
        self._dispatch()
        with self._lock:
            idle = not self._running and not any(
                j.status == JobStatus.PENDING.value for j in self.jobs
            )
        if idle:
            self.callbacks._call("on_queue_idle")
