"""Вкладка «Конвертация» (разделы 15, 36-39): контейнер, кодеки, субтитры.

Список контейнеров по умолчанию сокращён до самых популярных и разделён
по типу содержимого (видео/аудио) — какой из них показывать, определяется
автоматически по выбранному файлу (есть видеодорожка или нет). Полный
список мультиплексоров FFmpeg остаётся доступен через ту же настройку
«Показывать всё» (расширённый режим), что и для кодеков.

Предустановки объединены с общим хранилищем профилей (:mod:`core.profiles`),
которое использует и вкладка «Сжатие» — сохранённая здесь предустановка
доступна и там, и наоборот.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ...core.models import (
    AudioOptions,
    Job,
    MediaFile,
    Operation,
    SubtitleMode,
    SubtitleOptions,
    VideoOptions,
)
from ...core.profiles import Profile
from ..theming import font, pair
from ..widgets.surface import GAP, Card, button, muted
from ._controls import AudioPanel, VideoPanel
from ._dialogs import show_command
from ._estimate import OutputEstimator

_MANUAL_PRESET = "Ручные настройки"

# Раздел «Предустановки»: одним кликом настраивает режим и качество видео и
# аудио. Первая (используется по умолчанию) — чистый ремукс без
# перекодирования: качество не меняется, размер файла почти не растёт.
# Контейнер для неё дополнительно переключается на Matroska — единственный
# из популярных, который «принимает практически всё» без перекодирования
# (раздел совместимости), поэтому именно она надёжно выполняет обещание
# «просто сменить контейнер, не увеличивая размер».
_CONVERT_PRESETS: list[tuple[str, VideoOptions, AudioOptions, str | None]] = [
    (
        "Без потерь (только контейнер, без перекодирования)",
        VideoOptions(mode="copy"),
        AudioOptions(mode="copy"),
        "matroska",
    ),
    (
        "Высокое качество (минимальные потери)",
        VideoOptions(mode="encode", codec="auto", quality_mode="crf", crf=18.0, preset="slow"),
        AudioOptions(mode="encode", codec="auto", bitrate="256k"),
        None,
    ),
    (
        "Баланс (качество/размер)",
        VideoOptions(mode="encode", codec="auto", quality_mode="crf", crf=23.0, preset="medium"),
        AudioOptions(mode="encode", codec="auto", bitrate="160k"),
        None,
    ),
    (
        "Малый размер",
        VideoOptions(mode="encode", codec="auto", quality_mode="crf", crf=28.0, preset="fast"),
        AudioOptions(mode="encode", codec="auto", bitrate="128k"),
        None,
    ),
]
_PRESET_HINTS: dict[str, str] = {
    "Без потерь (только контейнер, без перекодирования)": (
        "Видео и аудио копируются как есть — качество исходное, размер файла почти не меняется."
    ),
    "Высокое качество (минимальные потери)": "Перекодирование почти без потери качества, файл может быть больше.",
    "Баланс (качество/размер)": "Разумный компромисс между качеством и размером файла.",
    "Малый размер": "Минимальный размер файла ценой заметной потери качества.",
}

# Раздел «самые популярные»: подпись -> имя мультиплексора FFmpeg.
_POPULAR_VIDEO_CONTAINERS: dict[str, str] = {
    "MP4 (.mp4)": "mp4",
    "MKV / Matroska (.mkv)": "matroska",
    "MOV / QuickTime (.mov)": "mov",
    "WebM (.webm)": "webm",
    "AVI (.avi)": "avi",
    "MPEG-TS (.ts)": "mpegts",
    "FLV (.flv)": "flv",
}
_POPULAR_AUDIO_CONTAINERS: dict[str, str] = {
    "MP3 (.mp3)": "mp3",
    "AAC (.aac)": "adts",
    "FLAC (.flac, без потерь)": "flac",
    "WAV (.wav, без потерь)": "wav",
}
_DEFAULT_CONTAINERS = list(_POPULAR_VIDEO_CONTAINERS.values())

_SUBTITLE_LABELS = {
    "Сохранить как есть": SubtitleMode.COPY,
    "Удалить субтитры": SubtitleMode.REMOVE,
    "Перекодировать в формат контейнера": SubtitleMode.CONVERT,
}


class ConvertTab(ctk.CTkScrollableFrame):
    """Прокручиваемая вкладка: содержимое никогда не обрезается по высоте,
    а кнопка запуска живёт в закреплённой панели действий главного окна."""

    def __init__(self, master, app, on_start) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_start = on_start
        self.media: MediaFile | None = None
        self._container_choices: dict[str, str] = dict(_POPULAR_VIDEO_CONTAINERS)
        self._selected_muxer: str | None = None
        # Контейнер, который навязывает текущая предустановка (например,
        # «Без потерь» всегда хочет Matroska) — имеет приоритет над
        # автоопределением по расширению исходного файла при выборе файла,
        # иначе смена файла тихо возвращала бы контейнер к MP4/исходному.
        self._preset_container: str | None = None
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

        self._build_preset_row()
        self._build_container_row()
        self._build_panels()
        self._build_subtitle_row()
        self._build_output_row()
        self._build_actions()
        self.video_panel.on_changed = self.estimator.schedule
        self.audio_panel.on_changed = self.estimator.schedule
        self._refresh_preset_values()
        self._apply_preset(_CONVERT_PRESETS[0][0])
        self.estimator.clear("выберите файл")

    # -- построение -----------------------------------------------------
    def _build_preset_row(self) -> None:
        """Предустановка и контейнер — одна карточка: это первое решение
        раздела, от которого зависит всё остальное."""
        card = Card(self, title="Предустановка", subtitle="С чего начать — параметры ниже можно поменять вручную")
        card.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        body = card.label_grid()
        self._preset_card = card

        ctk.CTkLabel(body, text="Набор", anchor="w", width=110).grid(row=0, column=0, sticky="w")
        self.preset_menu = ctk.CTkOptionMenu(
            body,
            values=[name for name, *_ in _CONVERT_PRESETS] + [_MANUAL_PRESET],
            command=self._apply_preset,
            width=320,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.preset_menu.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        button(body, "Сохранить как…", self._save_preset, width=150).grid(row=0, column=2)
        self.delete_preset_button = button(
            body, "Удалить", self._delete_preset, variant="danger", width=100
        )
        self.delete_preset_button.grid(row=0, column=3, padx=(8, 0))

        self.preset_hint_label = muted(body, "")
        self.preset_hint_label.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(4, 0))

    def _build_container_row(self) -> None:
        """Контейнер живёт в той же карточке, что и предустановка: он —
        часть одного решения «во что конвертируем»."""
        body = self._preset_card.body

        ctk.CTkLabel(body, text="Контейнер", anchor="w", width=110).grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        self.container_menu = ctk.CTkOptionMenu(
            body,
            values=list(_POPULAR_VIDEO_CONTAINERS.keys()),
            command=self._on_container_changed,
            width=240,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.container_menu.grid(row=2, column=1, columnspan=3, sticky="w", pady=(10, 0))

        # Подсказка про ремукс — хорошая новость, поэтому цветом состояния «ок».
        self.copy_hint_label = ctk.CTkLabel(
            body, text="", anchor="w", font=font("small"), text_color=pair("state.ok")
        )
        self.copy_hint_label.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(6, 0))

    def _build_panels(self) -> None:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=2, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure((0, 1), weight=1)

        self.video_panel = VideoPanel(frame, self.app)
        self.video_panel.grid(row=0, column=0, sticky="new", padx=(0, GAP))
        self.video_panel.on_user_change = self._on_panel_changed

        self.audio_panel = AudioPanel(frame, self.app)
        self.audio_panel.grid(row=0, column=1, sticky="new")
        self.audio_panel.on_user_change = self._on_panel_changed

    def _build_subtitle_row(self) -> None:
        card = Card(self, title="Субтитры")
        card.grid(row=3, column=0, sticky="ew", pady=(0, GAP))
        body = card.label_grid()

        ctk.CTkLabel(body, text="Дорожки субтитров", anchor="w", width=160).grid(
            row=0, column=0, sticky="w"
        )
        self.subtitle_menu = ctk.CTkOptionMenu(
            body,
            values=list(_SUBTITLE_LABELS),
            command=lambda _v: self.estimator.schedule(),
            width=240,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.subtitle_menu.grid(row=0, column=1, sticky="w")

    def _build_output_row(self) -> None:
        card = Card(self, title="Результат")
        card.grid(row=4, column=0, sticky="ew", pady=(0, GAP))
        body = card.label_grid()

        self.file_label = muted(body, "Файл не выбран")
        self.file_label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        ctk.CTkLabel(body, text="Сохранить в", anchor="w", width=110).grid(
            row=1, column=0, sticky="w"
        )
        self.output_entry = ctk.CTkEntry(body, font=font("small"))
        self.output_entry.grid(row=1, column=1, sticky="ew", padx=8)
        button(body, "Обзор", self._browse_output, width=90).grid(row=1, column=2)

    def _build_actions(self) -> None:
        # Кнопка запуска и ожидаемый размер вынесены в закреплённую панель
        # действий главного окна — они нужны всегда, независимо от прокрутки.
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=5, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure(1, weight=1)

        button(frame, "Показать команду FFmpeg", self._show_command, variant="ghost").grid(
            row=0, column=0
        )

    # -- предустановки -------------------------------------------------------
    def _refresh_preset_values(self) -> None:
        custom_names = [p.name for p in self.app.profiles.list() if not p.builtin]
        values = [name for name, *_ in _CONVERT_PRESETS] + custom_names + [_MANUAL_PRESET]
        current = self.preset_menu.get()
        self.preset_menu.configure(values=values)
        if current in values:
            self.preset_menu.set(current)
        self._update_delete_button()

    def _apply_preset(self, name: str) -> None:
        if name == _MANUAL_PRESET:
            self._preset_container = None
            self.preset_menu.set(_MANUAL_PRESET)
            self.preset_hint_label.configure(text="")
            self._update_delete_button()
            return

        builtin = next((p for p in _CONVERT_PRESETS if p[0] == name), None)
        if builtin is not None:
            _, video, audio, container = builtin
            hint = _PRESET_HINTS.get(name, "")
        else:
            profile = self.app.profiles.get(name)
            if profile is None:
                return
            video, audio, container = profile.video, profile.audio, profile.container
            hint = "Пользовательская предустановка."

        self._preset_container = container
        self._applying_preset = True
        try:
            self.video_panel.set_from(video)
            self.audio_panel.set_from(audio)
            if container:
                self.refresh_container_choices(preferred_muxer=container)
                self._sync_output_extension()
        finally:
            self._applying_preset = False
        self.preset_menu.set(name)
        self.preset_hint_label.configure(text=hint)
        self._update_copy_hint()
        self._update_delete_button()
        self.estimator.schedule()

    def _on_panel_changed(self) -> None:
        # Пользователь вручную поменял режим/кодек/качество — предустановка
        # больше не описывает текущие настройки точно.
        if getattr(self, "_applying_preset", False):
            return
        self._mark_manual()

    def _mark_manual(self) -> None:
        self._preset_container = None
        if self.preset_menu.get() != _MANUAL_PRESET:
            self.preset_menu.set(_MANUAL_PRESET)
            self.preset_hint_label.configure(text="")
        self._update_delete_button()

    def _update_delete_button(self) -> None:
        profile = self.app.profiles.get(self.preset_menu.get())
        enabled = profile is not None and not profile.builtin
        self.delete_preset_button.configure(state="normal" if enabled else "disabled")

    def _save_preset(self) -> None:
        dialog = ctk.CTkInputDialog(text="Название предустановки:", title="Сохранить предустановку")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        profile = Profile(
            name=name,
            video=self.video_panel.get_options(),
            audio=self.audio_panel.get_options(),
            container=self._current_container(),
        )
        self.app.profiles.save(profile)
        self._refresh_preset_values()
        self._preset_container = profile.container
        self.preset_menu.set(name)
        self.preset_hint_label.configure(text="Пользовательская предустановка.")
        self._update_delete_button()

    def _delete_preset(self) -> None:
        name = self.preset_menu.get()
        profile = self.app.profiles.get(name)
        if profile is None or profile.builtin:
            return
        if not messagebox.askyesno("Удалить предустановку", f"Удалить предустановку «{name}»?"):
            return
        self.app.profiles.delete(name)
        self._refresh_preset_values()
        self._apply_preset(_CONVERT_PRESETS[0][0])


    def on_shown(self) -> None:
        """Раздел открыли: досчитать оценку, отложенную пока он был скрыт."""
        self.estimator.on_shown()

    # -- капабилити -----------------------------------------------------
    def on_capabilities_ready(self) -> None:
        self.refresh_container_choices()
        self.video_panel.refresh_encoders()
        self.audio_panel.refresh_encoders()
        self._update_copy_hint()
        self.estimator.schedule()

    # -- список контейнеров ------------------------------------------------
    def _relevant_containers(self) -> dict[str, str]:
        """Раздел «Контейнер»: базовый режим — короткий список популярных
        форматов, разделённых по типу содержимого (видео/аудио) и подобранных
        автоматически под текущий файл; расширенный режим (та же настройка
        «Показывать всё», что и для кодеков) — полный список от FFmpeg."""
        show_all = bool(getattr(self.app.settings, "show_all_codecs", False))
        if show_all:
            caps = self.app.capabilities
            names = sorted({name for c in caps.muxer_list() for name in c.names})
            if names:
                return {name: name for name in names}
        is_audio_only = bool(self.media) and self.media.is_valid and not self.media.has_video
        return dict(_POPULAR_AUDIO_CONTAINERS if is_audio_only else _POPULAR_VIDEO_CONTAINERS)

    def refresh_container_choices(self, preferred_muxer: str | None = None) -> None:
        choices = self._relevant_containers()
        target = preferred_muxer or self._selected_muxer
        label = None
        if target:
            for existing_label, name in choices.items():
                if name == target:
                    label = existing_label
                    break
            if label is None:
                # Контейнер файла/предыдущий выбор не входит в подборку —
                # добавляем как есть, чтобы не потерять выбор пользователя.
                label = target
                choices[label] = target
        if label is None:
            label = next(iter(choices), None)
            target = choices.get(label) if label else None

        self._container_choices = choices
        self.container_menu.configure(values=list(choices.keys()))
        if label:
            self.container_menu.set(label)
        self._selected_muxer = target

    def _current_container(self) -> str:
        return self._container_choices.get(self.container_menu.get(), self._selected_muxer or "mp4")

    def _on_container_changed(self, label: str) -> None:
        self._selected_muxer = self._container_choices.get(label)
        if not getattr(self, "_applying_preset", False):
            # Пользователь сам выбрал контейнер — он важнее любой
            # предустановки при следующей смене файла.
            self._mark_manual()
        self._sync_output_extension()
        self._update_copy_hint()
        self.estimator.schedule()

    def _sync_output_extension(self) -> None:
        """При смене контейнера расширение результата должно меняться
        вместе с ним — иначе FFmpeg получает верный `-f`, но имя файла
        вводит в заблуждение (и часть плееров отказывается его открывать)."""
        current = self.output_entry.get().strip()
        if not current:
            return
        extension = self.app.compat.extension_for_muxer(self._current_container())
        if not extension:
            return
        new_path = Path(current).with_suffix("." + extension.lstrip("."))
        if str(new_path) != current:
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, str(new_path))

    def _update_copy_hint(self) -> None:
        if self.media is None:
            self.copy_hint_label.configure(text="")
            return
        decision = self.app.suggest_stream_copy(self.media, self._current_container())
        if decision.full_remux:
            self.copy_hint_label.configure(
                text="Возможен ремукс без перекодирования (Stream Copy)."
            )
        else:
            self.copy_hint_label.configure(text="")

    # -- файл --------------------------------------------------------------
    def set_file(self, media: MediaFile | None) -> None:
        self.media = media
        if media is None:
            self.file_label.configure(text="Файл не выбран")
            self.output_entry.delete(0, "end")
            self.copy_hint_label.configure(text="")
            self.refresh_container_choices()
            self.estimator.clear("выберите файл")
            return
        self.file_label.configure(text=media.name)
        # Контейнер, навязанный текущей предустановкой (например, «Без
        # потерь» -> Matroska), важнее расширения нового файла — иначе смена
        # файла молча возвращала бы контейнер к исходному формату.
        preferred = self._preset_container or self.app.compat.muxer_for_extension(media.path.suffix)
        # Смена файла может сменить тип содержимого (видео/аудио) — список
        # контейнеров пересчитывается автоматически под новый файл.
        self.refresh_container_choices(preferred_muxer=preferred)
        container = self._current_container()
        if not self.output_entry.get().strip():
            extension = self.app.compat.extension_for_muxer(container)
            suggestion = self.app.filesystem.suggest_output(
                media.path, self.app.settings.output_directory or None, extension, "_converted"
            )
            self.output_entry.insert(0, str(suggestion))
        self._update_copy_hint()
        self.estimator.schedule()

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
        subtitles = SubtitleOptions(mode=_SUBTITLE_LABELS.get(self.subtitle_menu.get(), SubtitleMode.COPY))
        output = self.output_entry.get().strip() or None if media is self.media else None
        return self.app.build_job(
            media,
            Operation.CONVERT.value,
            container=self._current_container(),
            video=video,
            audio=audio,
            subtitles=subtitles,
            output_path=output,
        )

    def _build_job(self) -> Job | None:
        if self.media is None:
            messagebox.showwarning("Конвертация", "Сначала выберите файл.")
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
