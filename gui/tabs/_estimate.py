"""Автоматическая оценка размера результата.

Размер пересчитывается сам при любом изменении параметров — кодека, качества,
контейнера, диапазона обрезки, выбранного файла. Кнопки «оценить» больше нет:
ответ на вопрос «сколько будет весить» должен быть на экране всегда, а не по
запросу.

Оценка стоит недёшево — это настоящее пробное кодирование нескольких секунд
(см. :mod:`services.estimate_service`), поэтому здесь четыре ограничителя:

* считает только открытый раздел. Выбор файла доходит до всех трёх разделов
  сразу, и без этой проверки один клик запускал бы три кодирования, из которых
  два никто не увидит;
* пауза после последнего изменения — движение ползунка качества не запускает
  десяток кодирований подряд;
* подпись задания: если параметры вернулись к уже оценённым, пробный запуск не
  повторяется;
* одно кодирование за раз; при новых правках идущее прерывается, а не
  дожидается своего конца впустую.
"""

from __future__ import annotations

import logging
import threading
import tkinter
from collections.abc import Callable
from dataclasses import astuple

from ...core.models import Job, format_size

log = logging.getLogger(__name__)

#: Пауза после последнего изменения параметров, мс.
DELAY_MS = 800

#: Насколько результат должен превысить исходник, чтобы это стоило подсветить.
LARGER_THAN_SOURCE = 1.05


def job_signature(job: Job) -> tuple:
    """Всё, от чего зависит размер результата. Совпало — переоценивать нечего."""
    return (
        tuple(job.input_files),
        job.container,
        job.video_encoder,
        job.audio_encoder,
        astuple(job.video),
        astuple(job.audio),
        astuple(job.trim),
        job.subtitles.mode.value if job.subtitles else None,
    )


def format_estimate(job: Job, result) -> tuple[str, str]:
    """Текст и уровень для строки размера."""
    if result is None or not result.ok:
        message = getattr(result, "message", "") if result is not None else ""
        return (f"не удалось оценить — {message}" if message else "не удалось оценить"), "muted"

    size = format_size(result.estimated_bytes)
    source_size = job.source.size if job.source else 0
    if not source_size:
        return f"≈ {size}", "info"

    percent = result.estimated_bytes / source_size * 100
    text = f"≈ {size}   ·   {percent:.0f} % от исходного"
    if result.estimated_bytes > source_size * LARGER_THAN_SOURCE:
        return f"{text}   ·   больше исходного", "warning"
    return text, "ok"


class OutputEstimator:
    """Следит за параметрами раздела и держит размер результата актуальным."""

    def __init__(
        self,
        widget: tkinter.Misc,
        app,
        build_job: Callable[[], Job | None],
        on_state: Callable[[str, str], None],
        delay_ms: int = DELAY_MS,
    ) -> None:
        self.widget = widget
        self.app = app
        self.build_job = build_job
        self.on_state = on_state
        self.delay_ms = delay_ms
        #: Открыт ли сейчас раздел. Назначается владельцем.
        self.is_active: Callable[[], bool] = lambda: True

        self.text = ""
        self.severity = "muted"
        self._after_id: str | None = None
        self._generation = 0
        self._busy = False
        self._pending = False
        self._stale = False
        self._signature: tuple | None = None

    # -- состояние ----------------------------------------------------------
    def state(self) -> tuple[str, str]:
        """Текущий текст и уровень — чтобы показать их при возврате в раздел."""
        return self.text, self.severity

    def clear(self, text: str = "") -> None:
        """Сбрасывает оценку: файл не выбран или параметры заведомо неполны."""
        self._cancel()
        self._signature = None
        self._stale = False
        self._generation += 1  # результат уже запущенной оценки не нужен
        self._publish(text, "muted")

    def on_shown(self) -> None:
        """Раздел открыли: досчитать то, что откладывали, пока он был скрыт."""
        if self._stale:
            self._stale = False
            self.schedule()

    # -- запуск -------------------------------------------------------------
    def schedule(self) -> None:
        """Параметры изменились — пересчитать после паузы."""
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay_ms, self._start)
        except tkinter.TclError:  # pragma: no cover - виджет уже уничтожен
            self._after_id = None

    def _cancel(self) -> None:
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except (tkinter.TclError, ValueError):  # pragma: no cover
            pass
        self._after_id = None

    def _start(self) -> None:
        self._after_id = None
        if not self.is_active():
            # Раздел закрыт — считать нечего, но и забывать нельзя.
            self._stale = True
            return
        try:
            job = self.build_job()
        except Exception:  # noqa: BLE001 - незаполненная форма не должна ломать раздел
            log.debug("задание для оценки не собралось", exc_info=True)
            job = None
        if job is None:
            self._signature = None
            self._publish("", "muted")
            return

        signature = job_signature(job)
        if signature == self._signature and self.text:
            return
        if self._busy:
            # Идущее кодирование уже про старые параметры — прерываем его,
            # чтобы не занимать процессор ответом, который никому не нужен.
            self._pending = True
            self._generation += 1
            self.app.estimate_service.cancel()
            return

        self._signature = signature
        self._busy = True
        self._generation += 1
        self._publish("оценка…", "muted")
        threading.Thread(
            target=self._work, args=(job, self._generation), daemon=True
        ).start()

    def _work(self, job: Job, generation: int) -> None:
        result = None
        try:
            result = self.app.estimate_service.estimate(job)
        except Exception:  # noqa: BLE001 - фоновый поток не должен падать молча
            log.exception("оценка размера не удалась")
        try:
            self.widget.after(0, self._finish, job, generation, result)
        except (tkinter.TclError, RuntimeError):  # pragma: no cover - окно закрыто
            pass

    def _finish(self, job: Job, generation: int, result) -> None:
        self._busy = False
        if self._pending:
            # Этот ответ про параметры, которые пользователь уже поменял.
            self._pending = False
            self._signature = None
            self.schedule()
            return
        if generation != self._generation:
            return  # параметры успели смениться — этот ответ уже не про них
        self._publish(*format_estimate(job, result))

    def _publish(self, text: str, severity: str) -> None:
        self.text = text
        self.severity = severity
        self.on_state(text, severity)
