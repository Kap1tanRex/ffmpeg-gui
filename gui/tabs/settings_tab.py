"""Раздел «Настройки» (разделы 32, 35, 46-47): FFmpeg, GPU, тема, очередь, диагностика.

Сделан прокручиваемым (``CTkScrollableFrame``), чтобы содержимое никогда
не обрезалось по высоте — независимо от размера окна или экрана.

Каждая группа настроек — отдельная карточка с заголовком и, где это помогает,
строкой-пояснением: то же деление экрана, что и в настройках ZapretGUI.
"""

from __future__ import annotations

from tkinter import filedialog

import customtkinter as ctk

from ...core.i18n import LANGUAGES, available_languages, t, tf
from ...app.events import Event
from ...app.state import APP_VERSION
from ...core.models import format_size
from ...core.naming import DEFAULT_TEMPLATE, TOKENS, name_values, render_name
from ...services import shell_integration
from ...services.ffmpeg_updater import SOURCE_LABELS, SOURCES, app_ffmpeg_dir
from ..theming import UI_SCALES, font, pair, scale_label
from ..widgets.status_strip import DOT, health_color
from ..widgets.scroll import ScrollFrame
from ..widgets.surface import GAP, PAGE_PAD, Card, button, muted, severity_color
from ..widgets.tooltip import attach_help

_OVERWRITE_SOURCE = {"ask": "Спрашивать", "overwrite": "Перезаписывать", "rename": "Переименовывать"}


def _overwrite_labels() -> dict[str, str]:
    return {key: t(label) for key, label in _OVERWRITE_SOURCE.items()}

#: Значение настройки ``theme`` -> подпись в интерфейсе.
_THEME_SOURCE = {"System": "Системная", "Light": "Светлая", "Dark": "Тёмная"}


def _theme_labels() -> dict[str, str]:
    return {key: t(label) for key, label in _THEME_SOURCE.items()}

#: Ширина колонки подписей — строки настроек читаются как таблица.
_LABEL_WIDTH = 285

#: Подпись источника сборок -> значение настройки.
def _source_by_label() -> dict[str, str]:
    return {t(label): key for key, label in SOURCE_LABELS.items()}


class SettingsTab(ScrollFrame):
    def __init__(self, master, app, on_settings_changed) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.on_settings_changed = on_settings_changed

        self.grid_columnconfigure(0, weight=1)

        self._build_ffmpeg_section()
        self._build_ffmpeg_update_section()
        self._build_gpu_section()
        self._build_codecs_section()
        self._build_quality_section()
        self._build_general_section()
        self._build_import_section()
        self._build_updates_section()
        self._build_automation_section()
        self._build_diagnostics_section()

        self._load_from_settings()
        self._update_gpu_label()
        self.show_encoder_probe()

        self.app.bus.subscribe(Event.GPU_DETECTED, lambda _gpus: self.after(0, self._update_gpu_label))
        bus = self.app.bus
        bus.subscribe(
            Event.FFMPEG_UPDATE_CHECKED, lambda state: self.after(0, self.show_ffmpeg_build, state)
        )
        bus.subscribe(
            Event.FFMPEG_UPDATE_PROGRESS,
            lambda received, total: self.after(0, self._ffmpeg_progress, received, total),
        )
        bus.subscribe(
            Event.FFMPEG_UPDATE_FINISHED,
            lambda ok, message: self.after(0, self._ffmpeg_installed, ok, message),
        )

    # -- вспомогательное ------------------------------------------------------
    def _card(self, row: int, title: str, subtitle: str = "") -> Card:
        card = Card(self, title=title, subtitle=subtitle)
        card.grid(row=row, column=0, sticky="ew", pady=(0, GAP))
        return card

    @staticmethod
    def _caption(master, text: str, row: int, width: int = _LABEL_WIDTH) -> ctk.CTkLabel:
        label = ctk.CTkLabel(master, text=text, anchor="w", width=width, font=font("body"))
        label.grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        return label

    @staticmethod
    def _menu(master, values: list[str], command) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(
            master,
            values=values,
            command=command,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )

    # -- FFmpeg (раздел 46) --------------------------------------------------
    def _build_ffmpeg_section(self) -> None:
        card = self._card(0, "FFmpeg", t("Исполняемый файл, которым выполняются все операции"))
        body = card.body
        body.grid_columnconfigure(1, weight=1)

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        status_row.grid_columnconfigure(1, weight=1)
        self.ffmpeg_status_dot = ctk.CTkLabel(
            status_row, text=DOT, width=12, font=font("small"), text_color=health_color("idle")
        )
        self.ffmpeg_status_dot.grid(row=0, column=0, padx=(0, 6), sticky="nw")
        self.ffmpeg_status_label = ctk.CTkLabel(
            status_row, text="", anchor="w", justify="left", font=font("small")
        )
        self.ffmpeg_status_label.grid(row=0, column=1, sticky="ew")
        # Путь к сборке бывает очень длинным: он переносится, а не выходит
        # за край карточки и не растягивает колонку подписей.
        self.ffmpeg_path_label = muted(status_row, "", wraplength=760)
        self.ffmpeg_path_label.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        path_row = ctk.CTkFrame(body, fg_color="transparent")
        path_row.grid(row=1, column=0, columnspan=3, sticky="ew")
        path_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(path_row, text=t("Путь к ffmpeg"), anchor="w", width=150).grid(
            row=0, column=0, sticky="w"
        )
        self.ffmpeg_path_entry = ctk.CTkEntry(path_row, font=font("small"))
        self.ffmpeg_path_entry.grid(row=0, column=1, sticky="ew", padx=8)
        button(path_row, t("Обзор"), self._browse_ffmpeg, width=90).grid(row=0, column=2)

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        button(buttons, t("Применить путь"), self._apply_ffmpeg_path, variant="primary").grid(
            row=0, column=0
        )
        button(buttons, t("Обновить capabilities"), self._refresh_capabilities).grid(
            row=0, column=1, padx=8
        )

    def _browse_ffmpeg(self) -> None:
        path = filedialog.askopenfilename(title=t("Укажите путь к ffmpeg"))
        if path:
            self.ffmpeg_path_entry.delete(0, "end")
            self.ffmpeg_path_entry.insert(0, path)

    def _apply_ffmpeg_path(self) -> None:
        path = self.ffmpeg_path_entry.get().strip()
        if path:
            self.app.set_ffmpeg_paths(path, str(self.app.ffmpeg.binaries.ffprobe or ""))
        self.update_ffmpeg_status()

    def _refresh_capabilities(self) -> None:
        self.app.redetect_capabilities()

    def update_ffmpeg_status(self) -> None:
        binaries = self.app.ffmpeg.binaries
        if binaries.available:
            text = tf("Найден: FFmpeg {version}", version=binaries.ffmpeg_version)
            path = str(binaries.ffmpeg)
            health = "ok"
        else:
            text = t("FFmpeg не найден — укажите путь вручную.")
            path = ""
            health = "error"
        self.ffmpeg_status_label.configure(text=text)
        self.ffmpeg_path_label.configure(text=path)
        self.ffmpeg_status_dot.configure(text_color=health_color(health))

    # -- обновление сборки FFmpeg ----------------------------------------------
    def _build_ffmpeg_update_section(self) -> None:
        card = self._card(
            1, t("Сборка FFmpeg"), t("Откуда брать обновления и куда их ставить")
        )
        body = card.label_grid()

        self._caption(body, t("Источник сборок"), 0)
        self.ffmpeg_source_menu = self._menu(
            body, [t(x) for x in SOURCE_LABELS.values()], self._on_ffmpeg_source_changed
        )
        self.ffmpeg_source_menu.grid(row=0, column=1, sticky="w", pady=5)

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        status_row.grid_columnconfigure(1, weight=1)
        self.ffmpeg_build_dot = ctk.CTkLabel(
            status_row, text=DOT, width=12, font=font("small"), text_color=health_color("idle")
        )
        self.ffmpeg_build_dot.grid(row=0, column=0, padx=(0, 6), sticky="nw")
        self.ffmpeg_build_label = ctk.CTkLabel(
            status_row,
            text=t("Сборка не проверялась."),
            anchor="w",
            justify="left",
            wraplength=620,
            font=font("small"),
        )
        self.ffmpeg_build_label.grid(row=0, column=1, sticky="ew")

        self.ffmpeg_progress = ctk.CTkProgressBar(body, height=6)
        self.ffmpeg_progress.set(0)
        self.ffmpeg_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.ffmpeg_progress.grid_remove()

        muted(
            body,
            tf(
                "Сборка распаковывается в {path} — приложение ищет её там само.",
                path=app_ffmpeg_dir(self.app.app_root),
            ),
            wraplength=620,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))
        button(buttons, t("Проверить сборку"), self._check_ffmpeg_build).grid(row=0, column=0)
        self.ffmpeg_install_button = button(
            buttons, t("Обновить FFmpeg"), self._install_ffmpeg_build, variant="primary"
        )
        self.ffmpeg_install_button.grid(row=0, column=1, padx=8)
        self.ffmpeg_install_button.configure(state="disabled")

    def _on_ffmpeg_source_changed(self, label: str) -> None:
        self.app.update_settings(ffmpeg_source=_source_by_label().get(label, SOURCES[0]))
        self._check_ffmpeg_build()

    def _check_ffmpeg_build(self) -> None:
        self.ffmpeg_build_dot.configure(text_color=health_color("busy"))
        self.ffmpeg_build_label.configure(text=t("Проверка сборки…"))
        self.ffmpeg_install_button.configure(state="disabled")
        self.app.check_ffmpeg_update()

    def _install_ffmpeg_build(self) -> None:
        build = self.app.ffmpeg_update_state.build
        if build is None:
            return
        self.ffmpeg_install_button.configure(state="disabled")
        self.ffmpeg_progress.set(0)
        self.ffmpeg_progress.grid()
        self.ffmpeg_build_dot.configure(text_color=health_color("busy"))
        self.ffmpeg_build_label.configure(text=tf("Загрузка {label}…", label=build.label))
        self.app.install_ffmpeg_update()

    def _ffmpeg_progress(self, received: int, total: int) -> None:
        if total:
            self.ffmpeg_progress.set(received / total)
        self.ffmpeg_build_label.configure(
            text=tf("Загружено {format_size}", format_size=format_size(received))
            + (f" из {format_size(total)}" if total else "")
        )

    def _ffmpeg_installed(self, ok: bool, message: str) -> None:
        self.ffmpeg_progress.grid_remove()
        self.update_ffmpeg_status()
        if ok:
            self.ffmpeg_build_dot.configure(text_color=health_color("ok"))
            self.ffmpeg_build_label.configure(text=tf("Установлена сборка: {message}", message=message))
        else:
            self.ffmpeg_build_dot.configure(text_color=health_color("error"))
            self.ffmpeg_build_label.configure(text=tf("Не удалось обновить: {message}", message=message))
            self.ffmpeg_install_button.configure(state="normal")

    def show_ffmpeg_build(self, state) -> None:
        """Результат проверки: что доступно и можно ли это поставить."""
        build = state.build
        if state.error:
            self.ffmpeg_build_dot.configure(text_color=health_color("error"))
            self.ffmpeg_build_label.configure(text=tf("Проверка не удалась: {error}", error=state.error))
            self.ffmpeg_install_button.configure(state="disabled")
            return
        if build is None:
            self.ffmpeg_build_dot.configure(text_color=health_color("idle"))
            self.ffmpeg_build_label.configure(text=t("Источник не предложил сборку."))
            self.ffmpeg_install_button.configure(state="disabled")
            return

        size = f", {format_size(build.size)}" if build.size else ""
        date = f" от {build.date_text}" if build.date_text else ""
        if state.newer:
            self.ffmpeg_build_dot.configure(text_color=health_color("warning"))
            self.ffmpeg_build_label.configure(
                text=tf("Доступна {label}{date}{size}. Установлена {installed}.", label=build.label, date=date, size=size, installed=state.installed)
            )
        else:
            self.ffmpeg_build_dot.configure(text_color=health_color("ok"))
            self.ffmpeg_build_label.configure(
                text=tf(
                    "Установленная сборка не старее: {installed}. В источнике — {source}{date}.",
                    installed=state.installed,
                    source=build.label,
                    date=date,
                )
            )
        # Поставить можно и не более новую: пользователь мог менять источник.
        self.ffmpeg_install_button.configure(state="normal")

    # -- видеоускоритель (раздел 35) ------------------------------------------
    def _build_gpu_section(self) -> None:
        card = self._card(2, t("Видеоускоритель (GPU)"), t("Аппаратное кодирование вместо процессора"))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        self.gpu_label = ctk.CTkLabel(
            body, text=t("Определение…"), anchor="w", justify="left", wraplength=520, font=font("small")
        )
        self.gpu_label.grid(row=0, column=0, sticky="ew")
        button(body, t("Обновить"), self._refresh_gpu, width=100).grid(row=0, column=1, sticky="e")

        self.auto_hw_check = ctk.CTkCheckBox(
            body,
            text=t(
                "Автоматически выбирать кодек под видеоускоритель "
                "(NVIDIA NVENC / AMD AMF / Intel Quick Sync)"
            ),
            command=self._on_auto_hw_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.auto_hw_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        attach_help(self.auto_hw_check, self.app, "auto_hardware")

        self.hw_decode_check = ctk.CTkCheckBox(
            body,
            text=t("Разбирать входное видео силами видеокарты (ускоряет чтение 4K и HEVC)"),
            command=self._on_hw_decode_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.hw_decode_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        attach_help(self.hw_decode_check, self.app, "hwaccel")

        # Итог настоящей пробы: какие аппаратные энкодеры завелись, а какие
        # нет и почему. Без этого «в списке нет NVENC» выглядит как поломка.
        self.encoder_probe_label = muted(body, "", wraplength=700, justify="left")
        self.encoder_probe_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def show_encoder_probe(self) -> None:
        probe = getattr(self.app.capabilities, "hardware_probe", {})
        if not probe:
            self.encoder_probe_label.configure(
                text=t("Аппаратные энкодеры ещё не проверены."), text_color=pair("fg.secondary")
            )
            return
        working = sorted(name for name, reason in probe.items() if not reason)
        broken = {name: reason for name, reason in probe.items() if reason}

        lines = []
        if working:
            lines.append(t("Проверено запуском — работают: ") + ", ".join(working))
        else:
            lines.append(t("Проверено запуском: ни один аппаратный энкодер не завёлся"))
        # Разные энкодеры отказывают по одной причине; показываем причины,
        # а не длинный список имён.
        by_reason: dict[str, list[str]] = {}
        for name, reason in sorted(broken.items()):
            # Причина лежит в кэше на исходном языке: он собран один раз и
            # переживает смену языка интерфейса.
            by_reason.setdefault(t(reason), []).append(name)
        for reason, names in by_reason.items():
            lines.append(f"{', '.join(names)} — {reason}")
        self.encoder_probe_label.configure(
            text="\n".join(lines),
            text_color=severity_color("ok" if working else "warn"),
        )

    def _on_hw_decode_changed(self) -> None:
        self.app.update_settings(hardware_decoding=bool(self.hw_decode_check.get()))

    def _refresh_gpu(self) -> None:
        self.gpu_label.configure(text=t("Определение…"))
        self.update_idletasks()
        self.app.redetect_gpu()
        self._update_gpu_label()

    def _update_gpu_label(self) -> None:
        from ...services.gpu_detector import summarize

        self.gpu_label.configure(text=summarize(self.app.gpu_info))

    def _on_auto_hw_changed(self) -> None:
        self.app.update_settings(auto_hardware_encoding=bool(self.auto_hw_check.get()))

    # -- список кодеков в выпадающих меню --------------------------------------
    def _build_codecs_section(self) -> None:
        card = self._card(3, t("Кодеки и контейнеры"), t("Что показывать в списках выбора"))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        muted(
            body,
            t(
                "По умолчанию в списках видны только H.264, H.265/HEVC и энкодеры "
                "видеокарты — каждый пункт подписан, на чём именно он выполняется "
                "(процессор или конкретная видеокарта). Список контейнеров при "
                "конвертации сокращён до популярных (MP4, MKV, MOV, WebM, MP3, AAC…) "
                "и подбирается автоматически под тип файла — видео или аудио. "
                "Включите расширенный режим, чтобы увидеть остальные кодеки "
                "(AV1, VP9, ProRes и др.) и все контейнеры, которые умеет эта сборка FFmpeg."
            ),
            wraplength=620,
        ).grid(row=0, column=0, sticky="ew")

        self.all_codecs_check = ctk.CTkCheckBox(
            body,
            text=t("Показывать все доступные кодеки и контейнеры (расширенный режим)"),
            command=self._on_all_codecs_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.all_codecs_check.grid(row=1, column=0, sticky="w", pady=(10, 0))

    def _on_all_codecs_changed(self) -> None:
        self.app.update_settings(show_all_codecs=bool(self.all_codecs_check.get()))
        self.on_settings_changed()

    # -- качество и подсказки (раздел 12, 32) ---------------------------------
    def _build_quality_section(self) -> None:
        card = self._card(4, t("Качество и подсказки"), t("Значения по умолчанию для новых заданий"))
        body = card.body
        body.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=t("CRF по умолчанию"), anchor="w", font=font("body")).grid(
            row=0, column=0, sticky="w"
        )
        self.crf_value_label = ctk.CTkLabel(
            header, text="CRF: 23", anchor="e", font=font("body", bold=True),
            text_color=pair("accent"),
        )
        self.crf_value_label.grid(row=0, column=1, sticky="e")

        scale_row = ctk.CTkFrame(body, fg_color="transparent")
        scale_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        scale_row.grid_columnconfigure(1, weight=1)

        muted(scale_row, t("Худшее\n(макс. сжатие)"), justify="center").grid(row=0, column=0)
        self.crf_slider = ctk.CTkSlider(
            scale_row, from_=0, to=51, number_of_steps=51, command=self._on_crf_slider
        )
        self.crf_slider.grid(row=0, column=1, sticky="ew", padx=12)
        muted(scale_row, t("Лучшее\n(мин. сжатие)"), justify="center").grid(row=0, column=2)
        attach_help(self.crf_slider, self.app, "default_crf")

        self.tooltips_check = ctk.CTkCheckBox(
            body,
            text=t("Показывать подсказки при наведении на параметры"),
            command=self._on_tooltips_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.tooltips_check.grid(row=2, column=0, sticky="w", pady=(12, 0))

    def _on_crf_slider(self, value: float) -> None:
        crf = round(value)
        self.crf_value_label.configure(text=f"CRF: {crf}")
        self.app.update_settings(default_crf=float(crf))
        self.on_settings_changed()

    def _on_tooltips_changed(self) -> None:
        self.app.update_settings(show_tooltips=bool(self.tooltips_check.get()))

    # -- общие настройки ------------------------------------------------------
    def _build_general_section(self) -> None:
        card = self._card(5, t("Общие"), t("Оформление, очередь и поведение при сохранении"))
        body = card.label_grid()

        self._caption(body, t("Тема оформления"), 0)
        self.theme_menu = self._menu(body, list(_theme_labels().values()), self._on_theme_changed)
        self.theme_menu.grid(row=0, column=1, sticky="w", pady=5)

        self._caption(body, t("Язык интерфейса"), 1)
        languages = available_languages()
        self.language_menu = self._menu(
            body, list(languages.values()), self._on_language_changed
        )
        self.language_menu.grid(row=1, column=1, sticky="w", pady=5)
        self._language_by_label = {label: code for code, label in languages.items()}
        self._language_at_start = self.app.settings.language
        self.language_hint = muted(body, "")
        self.language_hint.grid(row=1, column=2, sticky="w", padx=(12, 0))

        self._caption(body, t("Масштаб интерфейса"), 2)
        self.scale_menu = self._menu(body, list(UI_SCALES), self._on_scale_changed)
        self.scale_menu.grid(row=2, column=1, sticky="w", pady=5)
        attach_help(self.scale_menu, self.app, "ui_scale")
        # Масштаб, с которым окно было собрано: с ним и сравниваем выбор.
        self._scale_at_start = self.app.settings.ui_scale
        self.scale_hint = muted(body, "")
        self.scale_hint.grid(row=2, column=2, sticky="w", padx=(12, 0))

        self._caption(body, t("Параллельных заданий"), 3)
        self.parallel_menu = self._menu(body, ["1", "2", "3", "4"], self._on_parallel_changed)
        self.parallel_menu.grid(row=3, column=1, sticky="w", pady=5)

        self._caption(body, t("При существующем файле"), 4)
        self.overwrite_menu = self._menu(
            body, list(_overwrite_labels().values()), self._on_overwrite_changed
        )
        self.overwrite_menu.grid(row=4, column=1, sticky="w", pady=5)

        self._caption(body, t("Каталог результатов по умолчанию"), 5)
        output_frame = ctk.CTkFrame(body, fg_color="transparent")
        output_frame.grid(row=5, column=1, sticky="ew", pady=5)
        output_frame.grid_columnconfigure(0, weight=1)
        self.output_dir_entry = ctk.CTkEntry(output_frame, font=font("small"))
        self.output_dir_entry.grid(row=0, column=0, sticky="ew")
        self.output_dir_entry.bind("<FocusOut>", lambda _e: self._on_output_dir_changed())
        button(output_frame, t("Обзор"), self._browse_output_dir, width=90).grid(
            row=0, column=1, padx=(8, 0)
        )

        self._caption(body, t("Шаблон имени файла"), 6)
        self.template_entry = ctk.CTkEntry(body, font=font("small"))
        self.template_entry.grid(row=6, column=1, sticky="ew", pady=5)
        self.template_entry.bind("<FocusOut>", lambda _e: self._on_template_changed())
        self.template_entry.bind("<Return>", lambda _e: self._on_template_changed())
        attach_help(self.template_entry, self.app, "output_template")

        self.template_hint = muted(body, "")
        self.template_hint.grid(row=7, column=1, sticky="w", pady=(0, 4))
        muted(
            body,
            t("Доступно: ") + ", ".join("{" + t(name) + "}" for name in TOKENS),
            wraplength=430,
        ).grid(row=8, column=1, sticky="w", pady=(0, 2))

    def _on_template_changed(self) -> None:
        """Сохраняет шаблон и тут же показывает, какое имя из него выйдет."""
        template = self.template_entry.get().strip() or DEFAULT_TEMPLATE
        self.app.update_settings(output_template=template)
        self._refresh_template_hint()
        self.watch_entry.delete(0, "end")
        self.watch_entry.insert(0, settings.watch_folder)
        if settings.watch_enabled:
            self.watch_check.select()
        self._refresh_watch_status()

    def _refresh_template_hint(self) -> None:
        example = render_name(
            self.app.settings.output_template,
            name_values(t("Отпуск 2026"), "_compressed", codec="h264", height=1080, quality="crf23"),
        )
        self.template_hint.configure(text=tf("Например: {example}.mp4", example=example))

    def _on_theme_changed(self, value: str) -> None:
        self.app.update_settings(theme={v: k for k, v in _theme_labels().items()}.get(value, "System"))
        self.on_settings_changed()

    def _on_language_changed(self, value: str) -> None:
        """Запоминает язык; применяется он при следующем запуске.

        Подписи виджетов читаются в момент постройки окна, поэтому смена
        языка на лету потребовала бы пересобрать интерфейс целиком.
        """
        code = self._language_by_label.get(value, "ru")
        self.app.update_settings(language=code)
        self.language_hint.configure(
            text=t("Применится при следующем запуске") if code != self._language_at_start else "",
            text_color=severity_color("warn"),
        )

    def _refresh_scale_hint(self, changed: bool) -> None:
        self.scale_hint.configure(
            text=t("Применится при следующем запуске") if changed else "",
            text_color=severity_color("warn") if changed else pair("fg.secondary"),
        )

    def _on_scale_changed(self, value: str) -> None:
        """Запоминает масштаб; применяется он при следующем запуске.

        Менять масштаб на лету CustomTkinter умеет только наполовину: он
        пересчитывает размеры своих холстов, но не пересобирает раскладку —
        виджеты остаются на прежних местах, и карточки распадаются на
        обрывки. Поэтому масштаб выставляется до создания окна.
        """
        scale = UI_SCALES.get(value, 1.0)
        self.app.update_settings(ui_scale=scale)
        self._refresh_scale_hint(changed=abs(scale - self._scale_at_start) > 1e-6)

    def _on_parallel_changed(self, value: str) -> None:
        self.app.update_settings(parallel_jobs=int(value))

    def _on_overwrite_changed(self, value: str) -> None:
        self.app.update_settings(overwrite_policy={v: k for k, v in _overwrite_labels().items()}.get(value, "ask"))

    def _on_output_dir_changed(self) -> None:
        self.app.update_settings(output_directory=self.output_dir_entry.get().strip())

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(title=t("Каталог результатов"))
        if path:
            self.output_dir_entry.delete(0, "end")
            self.output_dir_entry.insert(0, path)
            self._on_output_dir_changed()

    # -- импорт (раздел 6) ----------------------------------------------------
    def _build_import_section(self) -> None:
        card = self._card(6, t("Импорт папок"), t("Что забирать из папки при перетаскивании"))
        # Флажки идут одной группой слева: тело карточки растягивает первую
        # колонку, поэтому они живут в собственной рамке.
        body = ctk.CTkFrame(card.body, fg_color="transparent")
        body.grid(row=0, column=0, sticky="w")

        self.recursive_check = ctk.CTkCheckBox(
            body, text=t("Рекурсивно"), command=self._on_import_changed,
            checkbox_width=18, checkbox_height=18, font=font("small"),
        )
        self.recursive_check.grid(row=0, column=0, sticky="w", padx=(0, 24))
        self.video_check = ctk.CTkCheckBox(
            body, text=t("Видео"), command=self._on_import_changed,
            checkbox_width=18, checkbox_height=18, font=font("small"),
        )
        self.video_check.grid(row=0, column=1, sticky="w", padx=(0, 24))
        self.audio_check = ctk.CTkCheckBox(
            body, text=t("Аудио"), command=self._on_import_changed,
            checkbox_width=18, checkbox_height=18, font=font("small"),
        )
        self.audio_check.grid(row=0, column=2, sticky="w", padx=(0, 24))
        self.images_check = ctk.CTkCheckBox(
            body, text=t("Изображения"), command=self._on_import_changed,
            checkbox_width=18, checkbox_height=18, font=font("small"),
        )
        self.images_check.grid(row=0, column=3, sticky="w")

    def _on_import_changed(self) -> None:
        self.app.update_settings(
            recursive_import=bool(self.recursive_check.get()),
            import_video=bool(self.video_check.get()),
            import_audio=bool(self.audio_check.get()),
            import_images=bool(self.images_check.get()),
        )

    # -- обновления --------------------------------------------------------------
    def _build_updates_section(self) -> None:
        card = self._card(
            7, t("Обновления"), tf("Установлена версия {version}", version=APP_VERSION)
        )
        body = card.label_grid()

        status_row = ctk.CTkFrame(body, fg_color="transparent")
        status_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        status_row.grid_columnconfigure(1, weight=1)
        self.update_dot = ctk.CTkLabel(
            status_row, text=DOT, width=12, font=font("small"), text_color=health_color("idle")
        )
        self.update_dot.grid(row=0, column=0, padx=(0, 6), sticky="nw")
        self.update_status_label = ctk.CTkLabel(
            status_row,
            text=t("Обновления ещё не проверялись."),
            anchor="w",
            justify="left",
            wraplength=620,
            font=font("small"),
        )
        self.update_status_label.grid(row=0, column=1, sticky="ew")

        self._caption(body, t("Репозиторий обновлений"), 1)
        self.repository_entry = ctk.CTkEntry(body, font=font("small"))
        self.repository_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.repository_entry.bind("<FocusOut>", lambda _e: self._on_repository_changed())

        self.updates_on_start_check = ctk.CTkCheckBox(
            body,
            text=t("Проверять обновления при запуске"),
            command=self._on_updates_on_start_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.updates_on_start_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.prereleases_check = ctk.CTkCheckBox(
            body,
            text=t("Показывать предварительные выпуски (dev, rc)"),
            command=self._on_prereleases_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.prereleases_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))
        button(buttons, t("Проверить обновления"), self._check_updates, variant="primary").grid(
            row=0, column=0
        )
        self.notes_button = button(buttons, t("Что нового"), self._show_notes)
        self.notes_button.grid(row=0, column=1, padx=8)
        self.notes_button.configure(state="disabled")

    def _on_repository_changed(self) -> None:
        self.app.update_settings(update_repository=self.repository_entry.get().strip())

    def _on_updates_on_start_changed(self) -> None:
        self.app.update_settings(check_updates_on_start=bool(self.updates_on_start_check.get()))

    def _on_prereleases_changed(self) -> None:
        self.app.update_settings(update_prereleases=bool(self.prereleases_check.get()))
        self._check_updates()

    def _check_updates(self) -> None:
        self.update_dot.configure(text_color=health_color("busy"))
        self.update_status_label.configure(text=t("Проверка обновлений…"))
        self.app.check_updates()

    def _show_notes(self) -> None:
        self.winfo_toplevel().show_updates(only_new=False)

    def show_update_state(self, state) -> None:
        """Результат проверки: цветная точка, строка и доступность «Что нового»."""
        checked = state.checked_at.strftime("%H:%M") if state.checked_at else ""
        if state.error:
            self.update_dot.configure(text_color=health_color("error"))
            self.update_status_label.configure(text=tf("Проверка не удалась: {error}", error=state.error))
        elif state.available:
            self.update_dot.configure(text_color=health_color("warning"))
            self.update_status_label.configure(
                text=tf("Доступна версия v{version} от {date_text}", version=state.latest.version, date_text=state.latest.date_text)
            )
        else:
            self.update_dot.configure(text_color=health_color("ok"))
            self.update_status_label.configure(
                text=t(f"Установлена последняя версия{f' · проверено в {checked}' if checked else ''}")
            )
        self.notes_button.configure(state="normal" if state.releases else "disabled")


    # -- автоматизация ---------------------------------------------------------
    def _build_automation_section(self) -> None:
        card = self._card(
            9, t("Автоматизация"), t("Папка наблюдения и пункт в контекстном меню проводника")
        )
        body = card.label_grid()

        self._caption(body, t("Папка наблюдения"), 0)
        watch_frame = ctk.CTkFrame(body, fg_color="transparent")
        watch_frame.grid(row=0, column=1, sticky="ew", pady=5)
        watch_frame.grid_columnconfigure(0, weight=1)
        self.watch_entry = ctk.CTkEntry(watch_frame, font=font("small"))
        self.watch_entry.grid(row=0, column=0, sticky="ew")
        self.watch_entry.bind("<FocusOut>", lambda _e: self._on_watch_changed())
        button(watch_frame, t("Обзор"), self._browse_watch_folder, width=90).grid(
            row=0, column=1, padx=(8, 0)
        )

        self.watch_check = ctk.CTkCheckBox(
            body,
            text=t("Ставить новые файлы в очередь автоматически"),
            command=self._on_watch_changed,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.watch_check.grid(row=1, column=1, sticky="w", pady=(6, 0))
        attach_help(self.watch_check, self.app, "watch_folder")

        self.watch_status = muted(body, "")
        self.watch_status.grid(row=2, column=1, sticky="w", pady=(2, 8))

        self._caption(body, t("Контекстное меню"), 3)
        shell_frame = ctk.CTkFrame(body, fg_color="transparent")
        shell_frame.grid(row=3, column=1, sticky="w", pady=5)
        self.shell_button = button(
            shell_frame, t("Добавить в меню проводника"), self._toggle_shell, width=250
        )
        self.shell_button.grid(row=0, column=0)
        self.shell_status = muted(body, "")
        self.shell_status.grid(row=4, column=1, sticky="w", pady=(2, 0))
        self._refresh_shell_status()

    def _browse_watch_folder(self) -> None:
        path = filedialog.askdirectory(title=t("Папка наблюдения"))
        if path:
            self.watch_entry.delete(0, "end")
            self.watch_entry.insert(0, path)
            self._on_watch_changed()

    def _on_watch_changed(self) -> None:
        self.app.update_settings(
            watch_folder=self.watch_entry.get().strip(),
            watch_enabled=bool(self.watch_check.get()),
        )
        self.app.apply_watch_settings()
        self._refresh_watch_status()

    def _refresh_watch_status(self) -> None:
        if self.app.watcher.active:
            text = "Наблюдение включено — новые файлы уходят в очередь"
            color = severity_color("ok")
        elif self.app.settings.watch_enabled:
            text = "Папка не указана или недоступна"
            color = severity_color("warn")
        else:
            text = "Наблюдение выключено"
            color = pair("fg.secondary")
        self.watch_status.configure(text=text, text_color=color)

    def _toggle_shell(self) -> None:
        """Пункт меню добавляется и убирается только по нажатию — сам собой
        в реестре ничего не появляется."""
        if not shell_integration.available():
            self.shell_status.configure(
                text=t("Доступно только в Windows"), text_color=severity_color("warn")
            )
            return
        try:
            if shell_integration.is_registered():
                shell_integration.unregister()
            else:
                shell_integration.register()
        except shell_integration.ShellIntegrationError as exc:
            self.shell_status.configure(text=str(exc), text_color=severity_color("error"))
            return
        self._refresh_shell_status()

    def _refresh_shell_status(self) -> None:
        if not shell_integration.available():
            self.shell_button.configure(state="disabled")
            self.shell_status.configure(text=t("Доступно только в Windows"))
            return
        registered = shell_integration.is_registered()
        self.shell_button.configure(
            text=t("Убрать из меню проводника")
            if registered
            else t("Добавить в меню проводника")
        )
        self.shell_status.configure(
            text=(
                t("Пункт «Открыть в FFmpeg GUI» есть в меню видео- и аудиофайлов")
                if registered
                else t("Запись только для текущего пользователя, права администратора не нужны")
            ),
            text_color=severity_color("ok") if registered else pair("fg.secondary"),
        )

    # -- диагностика (раздел 47) -----------------------------------------------
    def _build_diagnostics_section(self) -> None:
        card = self._card(10, t("Диагностика"), t("Сведения об окружении для отчёта об ошибке"))
        body = ctk.CTkFrame(card.body, fg_color="transparent")
        body.grid(row=0, column=0, sticky="w")

        button(body, t("Показать отчёт"), self._show_diagnostics).grid(row=0, column=0)
        button(body, t("Экспортировать в файл"), self._export_diagnostics).grid(
            row=0, column=1, padx=8
        )

    def _show_diagnostics(self) -> None:
        window = ctk.CTkToplevel(self)
        window.title("Диагностика")
        window.geometry("720x480")
        window.configure(fg_color=pair("bg.window"))
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)

        card = Card(window, title=t("Диагностика"))
        card.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        card.body.grid_rowconfigure(0, weight=1)

        textbox = ctk.CTkTextbox(card.body, wrap="word", font=font("mono"))
        textbox.grid(row=0, column=0, sticky="nsew")
        textbox.insert("1.0", self.app.diagnostics().to_text())
        textbox.configure(state="disabled")

        button(window, t("Закрыть"), window.destroy, variant="primary").grid(
            row=1, column=0, pady=PAGE_PAD
        )

    def _export_diagnostics(self) -> None:
        path = filedialog.asksaveasfilename(
            title=t("Экспорт диагностики"), defaultextension=".txt", initialfile="diagnostics.txt"
        )
        if path:
            self.app.export_diagnostics(path)

    # -- инициализация из настроек --------------------------------------------
    def _load_from_settings(self) -> None:
        settings = self.app.settings
        self.theme_menu.set(_theme_labels().get(settings.theme, t("Системная")))
        self.language_menu.set(LANGUAGES.get(settings.language, "Русский"))
        self.scale_menu.set(scale_label(settings.ui_scale))
        self.parallel_menu.set(str(settings.parallel_jobs))
        self.overwrite_menu.set(_overwrite_labels().get(settings.overwrite_policy, t("Спрашивать")))
        self.output_dir_entry.insert(0, settings.output_directory)
        self.template_entry.delete(0, "end")
        self.template_entry.insert(0, settings.output_template)
        self._refresh_template_hint()
        if settings.recursive_import:
            self.recursive_check.select()
        if settings.import_video:
            self.video_check.select()
        if settings.import_audio:
            self.audio_check.select()
        if settings.import_images:
            self.images_check.select()
        if settings.hardware_decoding:
            self.hw_decode_check.select()
        if settings.auto_hardware_encoding:
            self.auto_hw_check.select()
        if settings.show_all_codecs:
            self.all_codecs_check.select()
        if settings.show_tooltips:
            self.tooltips_check.select()
        if settings.check_updates_on_start:
            self.updates_on_start_check.select()
        if settings.update_prereleases:
            self.prereleases_check.select()
        self.repository_entry.insert(0, settings.update_repository)
        self.ffmpeg_source_menu.set(t(SOURCE_LABELS.get(settings.ffmpeg_source, SOURCE_LABELS[SOURCES[0]])))
        if not settings.update_repository.strip():
            self.update_status_label.configure(
                text=t("Репозиторий обновлений не задан — проверять нечего.")
            )
        self.crf_slider.set(settings.default_crf)
        self.crf_value_label.configure(text=f"CRF: {int(settings.default_crf)}")
        if self.app.ffmpeg.binaries.ffmpeg:
            self.ffmpeg_path_entry.insert(0, str(self.app.ffmpeg.binaries.ffmpeg))
        self.update_ffmpeg_status()
