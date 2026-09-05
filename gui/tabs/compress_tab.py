"""Вкладка «Сжатие» (разделы 11-14): профили и ручная настройка качества."""

from __future__ import annotations

from collections.abc import Callable
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ...core.models import Job, MediaFile, Operation
from ...core.profiles import Profile
from ..theming import font
from ..widgets.surface import GAP, Card, button, muted
from ._controls import AudioPanel, VideoPanel
from ._filters_panel import FilterPanel
from ._dialogs import show_command
from ._estimate import OutputEstimator


class CompressTab(ctk.CTkScrollableFrame):
    """Прокручиваемый раздел: содержимое никогда не обрезается по высоте,
    а кнопка запуска живёт в закреплённой панели действий главного окна."""

    def __init__(self, master, app, on_start) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_start = on_start
        self.media: MediaFile | None = None
        self._container_override: str | None = None
        # Размер результата пересчитывается сам; показывает его главное окно в
        # панели действий, поэтому раздел только сообщает наверх текст и
        # уровень. Колбэк назначается владельцем после создания раздела.
        self.on_estimate: "Callable[[str, str], None]" = lambda _text, _severity: None
        self.estimator = OutputEstimator(
            self,
            app,
            self._build_estimate_job,
            lambda text, severity: self.on_estimate(text, severity),
        )

        self.grid_columnconfigure(0, weight=1)

        self._build_profile_row()
        self._build_panels()
        self._build_output_row()
        self._build_actions()

        self.video_panel.on_changed = self.estimator.schedule
        self.audio_panel.on_changed = self.estimator.schedule
        self.filter_panel.on_changed = self.estimator.schedule
        self._refresh_profiles()
        self.estimator.clear("выберите файл")

    # -- построение -----------------------------------------------------
    def _build_profile_row(self) -> None:
        card = Card(self, title="Профиль", subtitle="Готовый набор параметров качества")
        card.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        self.profile_menu = ctk.CTkOptionMenu(
            body,
            values=["Ручные настройки"],
            command=self._on_profile_selected,
            width=240,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.profile_menu.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        button(body, "Сохранить как…", self._save_profile, width=150).grid(row=0, column=1)
        self.delete_profile_button = button(
            body, "Удалить", self._delete_profile, variant="danger", width=100
        )
        self.delete_profile_button.grid(row=0, column=2, padx=(8, 0))

    def _build_panels(self) -> None:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=1, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure((0, 1), weight=1)

        self.video_panel = VideoPanel(frame, self.app)
        self.video_panel.grid(row=0, column=0, sticky="new", padx=(0, GAP))

        self.audio_panel = AudioPanel(frame, self.app)
        self.audio_panel.grid(row=0, column=1, sticky="new")

        # Обработка картинки идёт отдельной строкой во всю ширину: полей в ней
        # больше, чем помещается в половину карточки.
        self.filter_panel = FilterPanel(frame, self.app)
        self.filter_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(GAP, 0))

    def _build_output_row(self) -> None:
        card = Card(self, title="Результат")
        card.grid(row=2, column=0, sticky="ew", pady=(0, GAP))
        body = card.label_grid()

        self.file_label = muted(body, "Файл не выбран")
        self.file_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ctk.CTkLabel(body, text="Сохранить в", anchor="w", width=110).grid(row=1, column=0, sticky="w")
        self.output_entry = ctk.CTkEntry(body, font=font("small"))
        self.output_entry.grid(row=1, column=1, sticky="ew", padx=8)
        button(body, "Обзор", self._browse_output, width=90).grid(row=1, column=2)

    def _build_actions(self) -> None:
        # Кнопка запуска и ожидаемый размер вынесены в закреплённую панель
        # действий главного окна — они нужны всегда, независимо от прокрутки.
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=3, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure(1, weight=1)

        button(frame, "Показать команду FFmpeg", self._show_command, variant="ghost").grid(
            row=0, column=0
        )

    def on_shown(self) -> None:
        """Раздел открыли: досчитать оценку, отложенную пока он был скрыт."""
        self.estimator.on_shown()

    # -- капабилити и профили --------------------------------------------
    def on_capabilities_ready(self) -> None:
        self.video_panel.refresh_encoders()
        self.audio_panel.refresh_encoders()
        self.estimator.schedule()

    def _refresh_profiles(self) -> None:
        names = ["Ручные настройки"] + [p.name for p in self.app.profiles.list()]
        self.profile_menu.configure(values=names)
        self._update_delete_button()

    def _on_profile_selected(self, name: str) -> None:
        if name == "Ручные настройки":
            self._container_override = None
            self._update_delete_button()
            return
        profile = self.app.profiles.get(name)
        if profile is None:
            return
        self.video_panel.set_from(profile.video)
        self.audio_panel.set_from(profile.audio)
        self._container_override = profile.container
        self._update_delete_button()
        self.estimator.schedule()

    def _update_delete_button(self) -> None:
        profile = self.app.profiles.get(self.profile_menu.get())
        enabled = profile is not None and not profile.builtin
        self.delete_profile_button.configure(state="normal" if enabled else "disabled")

    def _save_profile(self) -> None:
        dialog = ctk.CTkInputDialog(text="Название предустановки:", title="Сохранить предустановку")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        profile = Profile(
            name=name,
            video=self.video_panel.get_options(),
            audio=self.audio_panel.get_options(),
            container=self._container_override,
        )
        self.app.profiles.save(profile)
        self._refresh_profiles()
        self.profile_menu.set(name)
        self._update_delete_button()

    def _delete_profile(self) -> None:
        name = self.profile_menu.get()
        profile = self.app.profiles.get(name)
        if profile is None or profile.builtin:
            return
        if not messagebox.askyesno("Удалить предустановку", f"Удалить предустановку «{name}»?"):
            return
        self.app.profiles.delete(name)
        self._refresh_profiles()
        self.profile_menu.set("Ручные настройки")
        self._container_override = None
        self._update_delete_button()

    # -- файл --------------------------------------------------------------
    def set_file(self, media: MediaFile | None) -> None:
        self.media = media
        if media is None:
            self.file_label.configure(text="Файл не выбран")
            self.output_entry.delete(0, "end")
            self.estimator.clear("выберите файл")
            return
        self.file_label.configure(text=media.name)
        self.estimator.schedule()
        if not self.output_entry.get().strip():
            suggestion = self.app.suggest_output_path(
                media,
                media.path.suffix.lstrip("."),
                "_compressed",
                video=self.video_panel.get_options(),
                profile=self.profile_menu.get() if hasattr(self, "profile_menu") else "",
            )
            self.output_entry.insert(0, str(suggestion))

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(title="Сохранить как")
        if path:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, path)

    # -- сборка задания ------------------------------------------------------
    def build_job_for(self, media: MediaFile) -> Job | None:
        """Собирает задание для произвольного файла с текущими настройками
        панелей — используется как для текущего выбранного файла, так и для
        пакетной обработки нескольких отмеченных файлов сразу."""
        if media is None:
            return None
        video = self.video_panel.get_options()
        audio = self.audio_panel.get_options()
        output = self.output_entry.get().strip() or None if media is self.media else None
        job = self.app.build_job(
            media,
            Operation.COMPRESS.value,
            container=self._container_override,
            video=video,
            audio=audio,
            output_path=output,
        )
        if job is not None:
            self.filter_panel.apply_to(job)
        return job

    def _build_job(self) -> Job | None:
        if self.media is None:
            messagebox.showwarning("Сжатие", "Сначала выберите файл.")
            return None
        return self.build_job_for(self.media)

    def _show_command(self) -> None:
        job = self._build_job()
        if job is None:
            return
        show_command(self, self.app.preview_command(job))

    # -- ожидаемый размер результата -----------------------------------------
    def _build_estimate_job(self) -> Job | None:
        """Задание для оценки: без диалогов, их нельзя показывать по таймеру."""
        if self.media is None:
            return None
        return self.build_job_for(self.media)

    def start(self) -> None:
        job = self._build_job()
        if job is not None:
            self.on_start(job)
