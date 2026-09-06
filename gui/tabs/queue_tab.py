"""Раздел «Очередь» (раздел 24): список заданий, прогресс, управление.

Каждое задание — карточка со своим состоянием: цветная точка и подпись
статуса отвечают на вопрос «что с ним сейчас», полоса показывает прогресс,
а кнопки под ней доступны ровно тогда, когда действие имеет смысл.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk

from ...core.i18n import t
from ...core.models import Job, JobStatus, ProgressInfo
from ..theming import font, pair, radius
from ..widgets.status_strip import DOT, health_color
from ..widgets.scroll import ScrollFrame
from ..widgets.surface import GAP, Card, button, muted

_RETRYABLE = (JobStatus.FAILED.value, JobStatus.CANCELLED.value)

#: Статус задания -> уровень индикатора в карточке.
_STATUS_HEALTH = {
    JobStatus.PENDING.value: "idle",
    JobStatus.RUNNING.value: "busy",
    JobStatus.PAUSED.value: "warning",
    JobStatus.COMPLETED.value: "ok",
    JobStatus.FAILED.value: "error",
    JobStatus.CANCELLED.value: "warning",
    JobStatus.SKIPPED.value: "idle",
}


def open_folder(path: Path) -> None:
    """Открывает системный проводник с выделенным файлом, если возможно."""
    path = Path(path)
    try:
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except OSError:
        pass


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


class QueueTab(ctk.CTkFrame):
    def __init__(self, master, app, on_retry: Callable[[Job], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_retry = on_retry
        self._rows: dict[str, dict] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.card = Card(self, title=t("Задания"))
        self.card.grid(row=0, column=0, sticky="nsew", pady=(0, GAP))
        self.card.body.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self.card.body, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)
        button(toolbar, t("Очистить завершённые"), self._clear_finished).grid(row=0, column=0)

        self.list_frame = ScrollFrame(self.card.body, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", pady=(GAP, 0))
        self.list_frame.grid_columnconfigure(0, weight=1)

        self.empty_label = muted(self.list_frame, t("Очередь пуста — задания появятся здесь"))
        self.empty_label.grid(row=0, column=0, sticky="w", padx=4, pady=8)

    # -- обновления ------------------------------------------------------
    def refresh_queue(self) -> None:
        jobs = self.app.queue.jobs
        current_ids = {job.id for job in jobs}
        for job_id in list(self._rows):
            if job_id not in current_ids:
                self._rows.pop(job_id)["frame"].destroy()

        for index, job in enumerate(jobs):
            row = self._rows.get(job.id)
            if row is None:
                row = self._create_row(job)
                self._rows[job.id] = row
            row["frame"].grid(row=index, column=0, sticky="ew", pady=4, padx=2)
            self._update_row(job)

        if jobs:
            self.empty_label.grid_remove()
        else:
            self.empty_label.grid()

    def update_job(self, job: Job, info: ProgressInfo | None = None) -> None:
        if job.id not in self._rows:
            self.refresh_queue()
            return
        if info is not None:
            job.progress = info.fraction
        self._update_row(job)

    # -- построение строки -------------------------------------------------
    def _create_row(self, job: Job) -> dict:
        frame = ctk.CTkFrame(
            self.list_frame,
            fg_color=pair("bg.elevated"),
            corner_radius=radius("sm"),
            border_width=1,
            border_color=pair("border"),
        )
        frame.grid_columnconfigure(1, weight=1)

        dot = ctk.CTkLabel(frame, text=DOT, width=12, font=font("small"))
        dot.grid(row=0, column=0, padx=(12, 6), pady=(10, 0))

        label = ctk.CTkLabel(frame, text=job.label, anchor="w", font=font("body", bold=True))
        label.grid(row=0, column=1, sticky="ew", pady=(10, 0))

        status = ctk.CTkLabel(
            frame, text=job.status_title(), anchor="e", width=110, font=font("small", bold=True)
        )
        status.grid(row=0, column=2, sticky="e", padx=12, pady=(10, 0))

        bar = ctk.CTkProgressBar(frame, height=6)
        bar.set(0)
        bar.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 4))

        error_label = ctk.CTkLabel(
            frame,
            text="",
            anchor="w",
            justify="left",
            wraplength=560,
            text_color=pair("state.error"),
            font=font("small"),
        )
        error_label.grid(row=2, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 2))
        error_label.grid_remove()

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))

        cancel_btn = button(
            buttons, t("Отмена"), lambda: self.app.queue.cancel(job.id), compact=True, width=80
        )
        cancel_btn.grid(row=0, column=0, padx=4)

        retry_btn = button(buttons, t("Повторить"), lambda: self._retry(job), compact=True, width=90)
        retry_btn.grid(row=0, column=1, padx=4)

        remove_btn = button(
            buttons,
            t("Удалить"),
            lambda: (self.app.queue.remove(job.id), self.refresh_queue()),
            variant="danger",
            compact=True,
            width=80,
        )
        remove_btn.grid(row=0, column=2, padx=4)

        log_btn = button(
            buttons, t("Лог"), lambda: self.open_log(job), variant="ghost", compact=True, width=60
        )
        log_btn.grid(row=0, column=3, padx=4)

        folder_btn = button(
            buttons,
            t("Папка"),
            lambda: open_folder(Path(job.output_file)),
            variant="ghost",
            compact=True,
            width=70,
        )
        folder_btn.grid(row=0, column=4, padx=4)

        return {
            "frame": frame,
            "dot": dot,
            "label": label,
            "status": status,
            "bar": bar,
            "error_label": error_label,
            "cancel_btn": cancel_btn,
            "retry_btn": retry_btn,
            "remove_btn": remove_btn,
            "log_btn": log_btn,
            "folder_btn": folder_btn,
        }

    def _update_row(self, job: Job) -> None:
        row = self._rows[job.id]
        health = _STATUS_HEALTH.get(job.status, "idle")
        row["label"].configure(text=job.label)
        row["dot"].configure(text_color=health_color(health))
        row["status"].configure(text=job.status_title(), text_color=health_color(health))
        row["bar"].set(max(0.0, min(1.0, job.progress)))

        can_cancel = job.status in (
            JobStatus.PENDING.value,
            JobStatus.RUNNING.value,
            JobStatus.PAUSED.value,
        )
        row["cancel_btn"].configure(state="normal" if can_cancel else "disabled")
        can_retry = job.status in _RETRYABLE and self.on_retry is not None
        row["retry_btn"].configure(state="normal" if can_retry else "disabled")
        row["remove_btn"].configure(state="disabled" if job.is_active else "normal")
        row["log_btn"].configure(state="normal" if job.log_path else "disabled")
        row["folder_btn"].configure(
            state="normal" if job.status == JobStatus.COMPLETED.value else "disabled"
        )

        if job.status == JobStatus.FAILED.value and job.error:
            first_line = job.error.splitlines()[0]
            row["error_label"].configure(text=first_line)
            row["error_label"].grid()
        else:
            row["error_label"].grid_remove()

    def _retry(self, job: Job) -> None:
        if self.on_retry is None:
            return
        self.on_retry(job.clone_for_retry())
        self.refresh_queue()

    def _clear_finished(self) -> None:
        self.app.queue.clear_finished()
        self.refresh_queue()

    # -- вспомогательное -------------------------------------------------
    def open_log(self, job: Job) -> None:
        """Открывает технический лог задания системным средством."""
        if job.log_path:
            _open_path(Path(job.log_path))

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
