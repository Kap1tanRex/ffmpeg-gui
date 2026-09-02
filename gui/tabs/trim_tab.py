"""Вкладка «Обрезка» (разделы 16-18): диапазон, точность, предпросмотр кадра."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from ...core.models import AudioOptions, MediaFile, Operation, TrimOptions, VideoOptions, format_timecode, parse_timecode
from ..theming import font, pair
from ..widgets.surface import GAP, Card, button, muted
from ..widgets.timecode_entry import attach_timecode_mask
from ..widgets.tooltip import attach_help
from ._controls import AudioPanel, VideoPanel
from ._dialogs import show_command
from ._estimate import OutputEstimator

THUMBNAIL_COUNT = 6
THUMBNAIL_WIDTH = 140


class TrimTab(ctk.CTkScrollableFrame):
    """Прокручиваемая вкладка: содержимое никогда не обрезается по высоте,
    а кнопка запуска живёт в закреплённой панели действий главного окна."""

    def __init__(self, master, app, on_start) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_start = on_start
        self.media: MediaFile | None = None
        self._thumbnail_widgets: list[ctk.CTkButton] = []
        self._thumbnail_images: list = []
        self._thumbnail_generation = 0
        self._thumbnails_stale = False
        # См. CompressTab: размер результата пересчитывается сам, показывает
        # его главное окно в панели действий.
        self.on_estimate: "Callable[[str, str], None]" = lambda _text, _severity: None
        self.estimator = OutputEstimator(
            self,
            app,
            self._build_estimate_job,
            lambda text, severity: self.on_estimate(text, severity),
        )

        self.grid_columnconfigure(0, weight=1)

        self._build_file_row()
        self._build_range_row()
        self._build_thumbnails_row()
        self._build_panels()
        self._build_output_row()
        self._build_actions()
        self.video_panel.on_changed = self.estimator.schedule
        self.audio_panel.on_changed = self.estimator.schedule
        self._on_accurate_changed()
        self.estimator.clear("выберите файл")

    # -- построение -----------------------------------------------------
    def _build_file_row(self) -> None:
        """Карточка диапазона: файл, границы и способ обрезки — вместе,
        потому что решение по ним принимается одновременно."""
        self.range_card = Card(
            self, title="Диапазон", subtitle="Границы фрагмента и способ обрезки"
        )
        self.range_card.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        self.range_card.body.grid_columnconfigure(4, weight=1)

        self.file_label = muted(self.range_card.body, "Файл не выбран")
        self.file_label.grid(row=0, column=0, columnspan=5, sticky="ew", pady=(0, 8))

    def _build_range_row(self) -> None:
        body = self.range_card.body

        # Поля времени — в собственной рамке: иначе широкие строки-пояснения
        # ниже растянули бы колонку подписей и оторвали поля от их названий.
        fields = ctk.CTkFrame(body, fg_color="transparent")
        fields.grid(row=1, column=0, columnspan=5, sticky="w")

        ctk.CTkLabel(fields, text="Начало", anchor="w").grid(row=0, column=0, padx=(0, 10))
        self.start_entry = ctk.CTkEntry(
            fields, width=120, placeholder_text="00:00:00", font=font("small")
        )
        self.start_entry.grid(row=0, column=1, padx=(0, 24))
        attach_timecode_mask(self.start_entry)
        self.start_entry.bind("<KeyRelease>", lambda _e: self.estimator.schedule(), add="+")

        ctk.CTkLabel(fields, text="Конец", anchor="w").grid(row=0, column=2, padx=(0, 10))
        self.end_entry = ctk.CTkEntry(
            fields, width=120, placeholder_text="авто", font=font("small")
        )
        self.end_entry.grid(row=0, column=3)
        attach_timecode_mask(self.end_entry)
        self.end_entry.bind("<KeyRelease>", lambda _e: self.estimator.schedule(), add="+")

        muted(body, "Вводите только цифры — формат ЧЧ:ММ:СС подставляется автоматически.").grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(6, 0)
        )

        self.accurate_check = ctk.CTkCheckBox(
            body,
            text="Точная обрезка (перекодирование)",
            command=self._on_accurate_changed,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.accurate_check.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))
        attach_help(self.accurate_check, self.app, "trim_accurate")

        button(body, "Предпросмотр кадра", self._preview).grid(
            row=3, column=4, sticky="e", pady=(12, 0)
        )

        self.preview_label = muted(body, "")
        self.preview_label.grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))

    def _build_thumbnails_row(self) -> None:
        """Раздел «Миниатюры»: полоса кадров, равномерно распределённых по
        длительности файла. Клик по кадру подставляет его время в поле
        «Начало» или «Конец» — так границы обрезки видно глазами, а не
        только вслепую по таймкоду."""
        card = Card(self, title="Кадры файла")
        card.grid(row=2, column=0, sticky="ew", pady=(0, GAP))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text="Клик по кадру задаёт:", font=font("small")).pack(side="left")
        self.thumb_target_menu = ctk.CTkSegmentedButton(
            header, values=["Начало", "Конец"], font=font("small")
        )
        self.thumb_target_menu.set("Начало")
        self.thumb_target_menu.pack(side="left", padx=(10, 0))

        self.thumbnails_frame = ctk.CTkScrollableFrame(
            body, orientation="horizontal", height=116, fg_color="transparent"
        )
        # См. FileList: запрошенная высота держится только на внутреннем холсте.
        self.thumbnails_frame._parent_frame.configure(height=116)
        self.thumbnails_frame._parent_frame.grid_propagate(False)
        self.thumbnails_frame.grid(row=1, column=0, sticky="ew", pady=(GAP, 0))
        self.thumbnails_status = muted(self.thumbnails_frame, "")
        self.thumbnails_status.pack(side="left", padx=8, pady=8)

    def _refresh_thumbnails(self) -> None:
        # Шесть кадров — это шесть запусков FFmpeg. Пока раздел скрыт, они
        # никому не нужны: выбор файла доходит сюда при каждом клике в списке.
        if not self.estimator.is_active():
            self._thumbnails_stale = True
            return
        self._thumbnails_stale = False
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        for widget in self._thumbnail_widgets:
            widget.destroy()
        self._thumbnail_widgets.clear()
        self._thumbnail_images.clear()

        media = self.media
        if media is None or not media.has_video or not media.duration:
            self.thumbnails_status.configure(text="")
            return
        self.thumbnails_status.configure(text="Генерация превью…")

        def run() -> None:
            pairs = self.app.preview.extract_thumbnail_strip(
                media.path, media.duration, count=THUMBNAIL_COUNT, width=THUMBNAIL_WIDTH
            )
            self.after(0, self._show_thumbnails, generation, pairs)

        threading.Thread(target=run, daemon=True).start()

    def _show_thumbnails(self, generation: int, pairs: list) -> None:
        if generation != self._thumbnail_generation:
            return  # файл сменился, пока строились превью — результат устарел
        self.thumbnails_status.configure(text="" if pairs else "Не удалось построить превью.")
        for timestamp, path in pairs:
            try:
                image = Image.open(path)
            except OSError:
                continue
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
            self._thumbnail_images.append(ctk_image)
            button = ctk.CTkButton(
                self.thumbnails_frame,
                image=ctk_image,
                text=format_timecode(timestamp),
                compound="top",
                width=THUMBNAIL_WIDTH,
                height=100,
                corner_radius=6,
                fg_color="transparent",
                border_width=0,
                hover_color=pair("bg.hover"),
                text_color=pair("fg.secondary"),
                font=font("small"),
                command=lambda t=timestamp: self._apply_thumbnail(t),
            )
            button.pack(side="left", padx=4, pady=4)
            self._thumbnail_widgets.append(button)

    def _apply_thumbnail(self, timestamp: float) -> None:
        target = self.thumb_target_menu.get()
        entry = self.end_entry if target == "Конец" else self.start_entry
        entry.delete(0, "end")
        entry.insert(0, format_timecode(timestamp))
        self.estimator.schedule()

    def _build_panels(self) -> None:
        """Тот же выбор кодека (CPU/видеокарта), что и в разделах «Сжатие»
        и «Конвертация» — используется только при точной обрезке, когда
        видео и аудио всё равно перекодируются."""
        self.panels_hint = muted(
            self,
            "Кодек ниже применяется только при «Точной обрезке». "
            "При быстрой обрезке потоки копируются без перекодирования.",
        )
        self.panels_hint.grid(row=3, column=0, sticky="ew", pady=(0, 6))

        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=4, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure((0, 1), weight=1)

        self.video_panel = VideoPanel(frame, self.app)
        self.video_panel.grid(row=0, column=0, sticky="new", padx=(0, GAP))

        self.audio_panel = AudioPanel(frame, self.app)
        self.audio_panel.grid(row=0, column=1, sticky="new")

    def _on_accurate_changed(self) -> None:
        accurate = bool(self.accurate_check.get())
        mode = "encode" if accurate else "copy"
        for panel in (self.video_panel, self.audio_panel):
            panel.set_mode(mode)
            # Раздел 17: при обрезке режим кодека целиком определяется
            # флажком «Точная обрезка» — отдельный выбор copy/none здесь
            # избыточен и только запутывал бы пользователя.
            panel.mode_menu.configure(state="disabled")
            panel._on_mode_changed()
        self.estimator.schedule()

    def _build_output_row(self) -> None:
        card = Card(self, title="Результат")
        card.grid(row=5, column=0, sticky="ew", pady=(0, GAP))
        body = card.label_grid()

        ctk.CTkLabel(body, text="Сохранить в", anchor="w", width=110).grid(
            row=0, column=0, sticky="w"
        )
        self.output_entry = ctk.CTkEntry(body, font=font("small"))
        self.output_entry.grid(row=0, column=1, sticky="ew", padx=8)
        button(body, "Обзор", self._browse_output, width=90).grid(row=0, column=2)

    def _build_actions(self) -> None:
        # Кнопка запуска вынесена в закреплённую панель действий главного
        # окна (всегда видна, не зависит от размера окна), здесь остаётся
        # только предпросмотр команды.
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=6, column=0, sticky="ew", pady=(0, GAP))
        button(frame, "Показать команду FFmpeg", self._show_command, variant="ghost").grid(
            row=0, column=0
        )


    def on_shown(self) -> None:
        """Раздел открыли: досчитать оценку и построить отложенные кадры."""
        self.estimator.on_shown()
        if self._thumbnails_stale:
            self._refresh_thumbnails()

    # -- капабилити -----------------------------------------------------
    def on_capabilities_ready(self) -> None:
        self.video_panel.refresh_encoders()
        self.audio_panel.refresh_encoders()
        self.estimator.schedule()

    # -- файл --------------------------------------------------------------
    def set_file(self, media: MediaFile | None) -> None:
        self.media = media
        self.start_entry.delete(0, "end")
        self.end_entry.delete(0, "end")
        self.preview_label.configure(text="")
        if media is None:
            self.file_label.configure(text="Файл не выбран")
            self.output_entry.delete(0, "end")
            self.estimator.clear("выберите файл")
            self._refresh_thumbnails()
            return
        self.file_label.configure(text=f"{media.name}   •   длительность {format_timecode(media.duration)}")
        if not self.output_entry.get().strip():
            suggestion = self.app.filesystem.suggest_output(
                media.path, self.app.settings.output_directory or None, media.path.suffix.lstrip("."), "_trimmed"
            )
            self.output_entry.insert(0, str(suggestion))
        self.estimator.schedule()
        self._refresh_thumbnails()

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(title="Сохранить как")
        if path:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, path)

    # -- предпросмотр (раздел 18) --------------------------------------------
    def _preview(self) -> None:
        if self.media is None:
            return
        try:
            start = parse_timecode(self.start_entry.get()) or 0.0
        except ValueError as exc:
            messagebox.showerror("Обрезка", str(exc))
            return
        frame_path = self.app.preview.extract_frame(self.media.path, start)
        if frame_path is None:
            self.preview_label.configure(text="Не удалось извлечь кадр.")
            return
        self.preview_label.configure(text=f"Кадр сохранён: {frame_path}")
        self._open_file(frame_path)

    @staticmethod
    def _open_file(path) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except OSError:
            pass

    # -- сборка задания ------------------------------------------------------
    def _build_trim(self, quiet: bool = False) -> TrimOptions | None:
        """``quiet`` — для автоматической оценки: недописанный таймкод не
        повод показывать диалог об ошибке."""
        try:
            start = parse_timecode(self.start_entry.get())
            end = parse_timecode(self.end_entry.get())
        except ValueError as exc:
            if not quiet:
                messagebox.showerror("Обрезка", str(exc))
            return None
        return TrimOptions(
            enabled=True,
            start=start,
            end=end,
            accurate=bool(self.accurate_check.get()),
        )

    def build_job_for(self, media: MediaFile, quiet: bool = False):
        """Собирает задание для произвольного файла с текущими параметрами
        обрезки/качества — используется и для текущего файла, и для
        пакетной обработки нескольких отмеченных файлов (тот же диапазон
        времени применяется к каждому)."""
        if media is None:
            return None
        trim = self._build_trim(quiet=quiet)
        if trim is None:
            return None
        if trim.accurate:
            video = self.video_panel.get_options()
            video.mode = "encode"
            audio = self.audio_panel.get_options()
            audio.mode = "encode"
        else:
            video = VideoOptions(mode="copy")
            audio = AudioOptions(mode="copy")
        output = self.output_entry.get().strip() or None if media is self.media else None
        return self.app.build_job(
            media,
            Operation.TRIM.value,
            video=video,
            audio=audio,
            trim=trim,
            output_path=output,
        )

    def _build_job(self):
        if self.media is None:
            messagebox.showwarning("Обрезка", "Сначала выберите файл.")
            return None
        return self.build_job_for(self.media)

    def _show_command(self) -> None:
        job = self._build_job()
        if job is None:
            return
        show_command(self, self.app.preview_command(job))

    # -- ожидаемый размер результата -----------------------------------------
    def _build_estimate_job(self):
        """Задание для оценки: без диалогов, их нельзя показывать по таймеру."""
        if self.media is None:
            return None
        return self.build_job_for(self.media, quiet=True)

    def start(self) -> None:
        job = self._build_job()
        if job is not None:
            self.on_start(job)
