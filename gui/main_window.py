"""Главное окно приложения.

Разделы ТЗ: 5 (layout), 6 (импорт), 24 (очередь), 26 (прогресс),
27 (диалог ошибок), 31 (защита от перезаписи), 46 (FFmpeg не найден),
51 (горячие клавиши), 53 (статистика), 56 (доступность).

Оформление повторяет ZapretGUI: слева фиксированный сайдбар с разделами,
справа — шапка раздела, общий список файлов, содержимое раздела, закреплённая
панель действий; внизу окна — строка состояния с индикаторами. Логика работы
не изменилась: навигация выставляет тот же API, что и прежний ``CTkTabview``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..app.application import Application
from ..app.events import Event
from ..app.state import APP_VERSION
from ..core.models import Job, JobStatus, MediaFile, OverwritePolicy, ProgressInfo
from . import theming
from .tabs.compress_tab import CompressTab
from .tabs.convert_tab import ConvertTab
from .tabs.info_tab import InfoTab
from .tabs.queue_tab import QueueTab
from .tabs.settings_tab import SettingsTab
from .tabs.trim_tab import TrimTab
from .theming import font, pair
from .update_window import UpdateWindow
from .widgets.drop_zone import DND_AVAILABLE, DropZone
from .widgets import hint
from .widgets.file_list import FileList
from .widgets.navigation import NavigationView
from .widgets.progress_bar import ProgressPanel
from .widgets.status_strip import StatusStrip
from .widgets.surface import CARD_PADX, GAP, PAGE_PAD, Card, button, muted, severity_color

log = logging.getLogger(__name__)

WINDOW_MIN_SIZE = (1120, 760)

#: Значки разделов — из системных шрифтов, чтобы не тянуть графические ресурсы.
SECTION_ICONS = {
    "compress": "▣",
    "convert": "⇄",
    "trim": "✂",
    "info": "ⓘ",
    "queue": "☰",
    "settings": "⚙",
}

#: Подзаголовок раздела: одна строка о том, что здесь делают.
SECTION_HINTS = {
    "compress": "Уменьшить размер файла, сохранив приемлемое качество",
    "convert": "Сменить контейнер и кодеки — при возможности без перекодирования",
    "trim": "Вырезать фрагмент по таймкоду: быстро копированием или точно",
    "info": "Полный разбор файла по данным FFprobe",
    "queue": "Задания, прогресс, повтор и логи",
    "settings": "FFmpeg, видеоускоритель, кодеки, тема и поведение очереди",
}

#: Разделы, которые работают с выбранным файлом — только на них нужен список.
FILE_SECTIONS = ("compress", "convert", "trim", "info")

#: Индикаторы строки состояния (идентификатор, подпись).
STATUS_CHIPS = (
    ("job", "Задание"),
    ("queue", "Очередь"),
    ("gpu", "GPU"),
    ("ffmpeg", "FFmpeg"),
)

try:  # интеграция tkinterdnd2 с корневым окном CustomTkinter
    from tkinterdnd2 import TkinterDnD  # type: ignore

    class _RootWindow(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore[misc]
        """CTk с поддержкой Drag & Drop."""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception as exc:  # pragma: no cover
                log.warning("tkinterdnd2 не инициализирован: %s", exc)

except ImportError:  # pragma: no cover

    class _RootWindow(ctk.CTk):  # type: ignore[no-redef]
        """CTk без Drag & Drop: доступен импорт кнопками."""


class MainWindow(_RootWindow):
    """Окно верхнего уровня."""

    def __init__(self, app: Application) -> None:
        # Тема применяется до создания виджетов: CustomTkinter читает её
        # значения в момент построения каждого виджета, а не при отрисовке.
        theming.apply(app.settings.theme, user_dir=app.store.directory / "themes")
        # Масштаб выставляется до создания окна: CustomTkinter умеет менять его
        # и на лету, но геометрия окна тогда останется от прежнего значения.
        theming.set_ui_scale(app.settings.ui_scale)

        super().__init__()
        self.app = app
        self.tr = app.translator
        self._active_job: Job | None = None
        self._section_by_title: dict[str, str] = {}
        self._ffmpeg_dialog_open = False

        self.title(f"{self.tr('app.title', 'FFmpeg GUI')} {APP_VERSION}")
        self.geometry("1340x920")
        self.minsize(*WINDOW_MIN_SIZE)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_navigation()
        self._build_header()
        self._build_files_card()
        self._build_tabs()
        self._build_action_bar()
        self._build_progress()
        self._build_status()
        self.tabs.set_command(self._on_section_changed)
        self._on_section_changed()
        # Окно подсказки и общее правило «движется мышь — подсказки нет»
        # ставятся один раз на всё приложение.
        hint.prepare(self)

        self._subscribe()
        self._bind_shortcuts()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.app.initialize)
        # Наблюдение включается после старта: в конструкторе фоновый
        # поток мог бы прислать событие в ещё не собранное окно.
        self.after(1200, self.app.apply_watch_settings)

    # -- построение ---------------------------------------------------------
    def _build_navigation(self) -> None:
        self.nav = NavigationView(
            self,
            title=self.tr("app.title", "FFmpeg GUI"),
            subtitle=self.tr("app.subtitle", "Кодирование, конвертация и обрезка"),
            footer=f"Версия {APP_VERSION}",
        )
        self.nav.grid(row=0, column=0, sticky="nsew")
        # Логика окна обращается к навигации так же, как раньше к CTkTabview.
        self.tabs = self.nav
        self.content = self.nav.content

        self.nav.add_footer_widget(
            button(
                self.nav.footer_frame,
                f"{self.tr('app.help', 'Справка')}  ·  F1",
                self.show_help,
                variant="ghost",
                anchor="w",
            )
        )
        self.nav.add_footer_widget(
            button(
                self.nav.footer_frame,
                "Лог FFmpeg  ·  Ctrl+L",
                self.show_logs,
                variant="ghost",
                anchor="w",
            )
        )

        # Пока обновления нет, кнопки нет: постоянная надпись «обновлений нет»
        # занимала бы место и ничего не сообщала.
        self.update_button = button(
            self.nav.footer_frame,
            "",
            self.show_updates,
            variant="link",
            anchor="w",
        )
        self.nav.add_footer_widget(self.update_button)
        self.update_button.grid_remove()

    def _build_header(self) -> None:
        """Шапка раздела: имя открытого раздела и строка о его назначении."""
        header = ctk.CTkFrame(self.content, fg_color="transparent")
        # Заголовок раздела выравнивается не по краю карточек, а по их
        # заголовкам: «Сжатие» и «Файлы» должны стоять на одной вертикали.
        header.grid(
            row=0, column=0, sticky="ew", padx=(PAGE_PAD + CARD_PADX, PAGE_PAD), pady=(PAGE_PAD, 4)
        )
        header.grid_columnconfigure(0, weight=1)

        self.section_title = ctk.CTkLabel(
            header, text="", anchor="w", font=font("hero", bold=True)
        )
        self.section_title.grid(row=0, column=0, sticky="ew")

        self.section_hint = muted(header, "")
        self.section_hint.grid(row=1, column=0, sticky="ew", pady=(2, 0))

    def _build_files_card(self) -> None:
        self.files_card = Card(self.content, title=self.tr("import.files", "Файлы"))
        self.files_card.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=(4, GAP))
        body = self.files_card.body
        body.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(body, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.grid_columnconfigure(3, weight=1)

        button(
            toolbar, self.tr("import.add_file", "Добавить файл"), self.add_files, variant="primary"
        ).grid(row=0, column=0, padx=(0, 8))
        button(toolbar, self.tr("import.add_folder", "Добавить папку"), self.add_folder).grid(
            row=0, column=1, padx=(0, 8)
        )
        button(toolbar, self.tr("import.clear", "Очистить"), self.clear_files).grid(
            row=0, column=2, padx=(0, 8)
        )

        self.import_hint = muted(toolbar, "")
        self.import_hint.grid(row=0, column=3, sticky="e", padx=(8, 12))

        self._select_all_var = ctk.BooleanVar(value=False)
        self.select_all_check = ctk.CTkCheckBox(
            toolbar,
            text=self.tr("import.select_all", "Отметить все"),
            variable=self._select_all_var,
            command=self._on_select_all,
            checkbox_width=18,
            checkbox_height=18,
            font=font("small"),
        )
        self.select_all_check.grid(row=0, column=4, sticky="e")

        self.drop_zone = DropZone(
            body,
            on_drop=self.on_drop,
            on_click=self.add_files,
            title=self.tr("import.drop_here", "Перетащите файлы сюда"),
            hint=self.tr("import.drop_hint", "или воспользуйтесь кнопками выше"),
        )
        self.drop_zone.grid(row=1, column=0, sticky="ew", pady=(GAP, 0))

        self.file_list = FileList(
            body,
            on_select=self.select_file,
            on_remove=self.app.remove_file,
            on_check_changed=self._sync_action_bar,
            empty_text=self.tr("import.empty", "Список файлов пуст"),
            height=124,
        )
        self.file_list.grid(row=2, column=0, sticky="nsew", pady=(GAP, 0))

    def _on_select_all(self) -> None:
        self.file_list.check_all(bool(self._select_all_var.get()))

    def _build_tabs(self) -> None:
        names = {
            "compress": self.tr("tab.compress", "Сжатие"),
            "convert": self.tr("tab.convert", "Конвертация"),
            "trim": self.tr("tab.trim", "Обрезка"),
            "info": self.tr("tab.info", "Информация"),
            "queue": self.tr("tab.queue", "Очередь"),
            "settings": self.tr("tab.settings", "Настройки"),
        }
        self._section_by_title = {title: key for key, title in names.items()}
        for key, title in names.items():
            self.tabs.add(title, SECTION_ICONS.get(key, ""))

        self.compress_tab = CompressTab(self.tabs.tab(names["compress"]), self.app, self.enqueue)
        self.convert_tab = ConvertTab(self.tabs.tab(names["convert"]), self.app, self.enqueue)
        self.trim_tab = TrimTab(self.tabs.tab(names["trim"]), self.app, self.enqueue)
        self.info_tab = InfoTab(self.tabs.tab(names["info"]), self.app)
        self.queue_tab = QueueTab(self.tabs.tab(names["queue"]), self.app, on_retry=self.enqueue)
        self.settings_tab = SettingsTab(
            self.tabs.tab(names["settings"]), self.app, self.on_settings_changed
        )

        for tab_name, widget in (
            (names["compress"], self.compress_tab),
            (names["convert"], self.convert_tab),
            (names["trim"], self.trim_tab),
            (names["info"], self.info_tab),
            (names["queue"], self.queue_tab),
            (names["settings"], self.settings_tab),
        ):
            container = self.tabs.tab(tab_name)
            container.grid_columnconfigure(0, weight=1)
            container.grid_rowconfigure(0, weight=1)
            widget.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=0)

        # Разделы, которые собирают задание: по ним ищется активный раздел
        # для кнопки запуска, пакетной обработки и оценки размера.
        self._job_tabs = {
            names["compress"]: self.compress_tab,
            names["convert"]: self.convert_tab,
            names["trim"]: self.trim_tab,
        }
        # Разделы считают ожидаемый размер сами; окно показывает его в панели
        # действий, а сам расчёт идёт только в открытом разделе.
        for tab in self._job_tabs.values():
            tab.on_estimate = lambda text, severity, t=tab: self.show_estimate(t, text, severity)
            tab.estimator.is_active = lambda t=tab: self._active_job_tab() is t

        self.operation_tabs = (self.compress_tab, self.convert_tab, self.trim_tab, self.info_tab)
        self._start_tab_names = {
            names["compress"]: self.tr("action.compress", "▶  Сжать"),
            names["convert"]: self.tr("action.convert", "▶  Конвертировать"),
            names["trim"]: self.tr("action.trim", "▶  Обрезать"),
        }

    def _build_action_bar(self) -> None:
        """Закреплённая панель действий: кнопка запуска видна всегда,
        независимо от размера окна и содержимого текущего раздела."""
        self.action_card = Card(self.content, pady=12)
        self.action_card.grid(row=5, column=0, sticky="ew", padx=PAGE_PAD, pady=(GAP, GAP))
        body = self.action_card.body
        body.grid_columnconfigure(0, weight=1)

        self.action_hint_label = muted(body, "")
        self.action_hint_label.grid(row=0, column=0, sticky="w", padx=(2, 12))

        # Ожидаемый размер результата — рядом с кнопкой запуска: это последнее,
        # на что смотрят перед нажатием, и панель действий видна всегда.
        estimate_block = ctk.CTkFrame(body, fg_color="transparent")
        estimate_block.grid(row=0, column=1, sticky="e", padx=(0, 18))
        muted(estimate_block, "Ожидаемый размер", anchor="e").grid(row=0, column=0, sticky="e")
        self.estimate_label = ctk.CTkLabel(
            estimate_block, text="—", anchor="e", font=font("body", bold=True)
        )
        self.estimate_label.grid(row=1, column=0, sticky="e")

        self.action_button = button(
            body,
            self.tr("action.none", "▶  Запустить"),
            self._on_action_button,
            variant="primary",
            width=230,
            height=40,
            font=font("large", bold=True),
        )
        self.action_button.grid(row=0, column=3, sticky="e")

        # Склейка появляется только тогда, когда её есть из чего собрать:
        # для одного файла кнопка бессмысленна и место не занимает.
        self.join_button = button(
            body, "⧉  Склеить", self.join_checked, width=130, height=40
        )
        self.join_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        self.join_button.grid_remove()

    def _watch_files(self, paths) -> None:
        """Файлы из папки наблюдения: импорт и постановка в очередь.

        Настройки берутся у открытого раздела операций. Если открыт какой-то
        другой раздел, файлы просто попадают в список — ставить их в очередь
        неизвестно с какими параметрами хуже, чем не ставить вовсе.
        """
        self.app.import_paths(paths, async_probe=False)
        tab = self._active_job_tab()
        if tab is None:
            self._set_status(f"Из папки наблюдения добавлено файлов: {len(paths)}")
            return

        known = {str(media.path): media for media in self.app.state.files}
        jobs = []
        for path in paths:
            media = known.get(str(path))
            if media is None:
                continue
            job = tab.build_job_for(media)
            if job is not None:
                jobs.append(job)
        for job in jobs:
            self.app.enqueue(job, autostart=False)
        if jobs:
            self.app.queue.start()
            self.queue_tab.refresh_queue()
        self._set_status(f"Из папки наблюдения в очередь: {len(jobs)}")

    def join_checked(self) -> None:
        """Склеивает отмеченные файлы в один — по порядку в списке."""
        files = self.app.state.files
        medias = [files[i] for i in self.file_list.checked_indices() if 0 <= i < len(files)]
        job = self.app.build_concat_job(medias)
        if job is None:
            return
        lossless = bool(job.concat_list)
        if not messagebox.askyesno(
            "Склейка",
            f"Склеить файлов: {len(medias)}\n\n"
            + (
                "Куски совпадают по кодекам — склейка пройдёт без "
                "перекодирования, мгновенно и без потери качества."
                if lossless
                else "Куски различаются по кодекам или размеру кадра, поэтому "
                "их придётся перекодировать — это займёт столько же времени, "
                "сколько обычное кодирование."
            )
            + f"\n\nРезультат: {job.output_file}",
        ):
            return
        self.enqueue(job)

    # -- ожидаемый размер результата ------------------------------------------
    def _active_job_tab(self):
        """Открытый раздел, который умеет собирать задание, либо ``None``."""
        return self._job_tabs.get(self.tabs.get())

    def show_estimate(self, tab, text: str, severity: str = "muted") -> None:
        """Показывает оценку раздела — если он сейчас открыт.

        Раздел мог ответить, когда пользователь уже переключился: чужую оценку
        показывать нельзя.
        """
        if self._active_job_tab() is not tab:
            return
        self._render_estimate(text, severity)

    def _render_estimate(self, text: str, severity: str) -> None:
        self.estimate_label.configure(
            text=text or "—",
            text_color=severity_color(severity) if severity != "muted" else pair("fg.secondary"),
        )

    def _sync_estimate(self) -> None:
        """Возврат в раздел: показать посчитанное раньше и досчитать отложенное."""
        tab = self._active_job_tab()
        if tab is None:
            self._render_estimate("", "muted")
            return
        self._render_estimate(*tab.estimator.state())
        tab.on_shown()

    def _sync_action_bar(self, *_args) -> None:
        """Подстраивает подпись и доступность кнопки под активный раздел и
        число отмеченных для пакетной обработки файлов. Вызывается при смене
        раздела и при отметке/снятии флажков."""
        current = self.tabs.get()
        label = self._start_tab_names.get(current)
        checked = len(self.file_list.checked_indices())
        # Склеивать есть что только внутри разделов операций и от двух файлов.
        if label is not None and checked >= 2:
            self.join_button.grid()
        else:
            self.join_button.grid_remove()

        if label is None:
            self.action_button.configure(
                text=self.tr("action.none", "▶  Запустить"), state="disabled"
            )
            self.action_hint_label.configure(text="")
            return

        if checked >= 2:
            self.action_button.configure(
                text=self.tr("action.batch", "▶  Обработать файлов: {n}").format(n=checked),
                state="normal",
            )
            self.action_hint_label.configure(
                text=self.tr(
                    "action.batch_hint",
                    "Настройки текущего раздела применятся ко всем отмеченным файлам",
                )
            )
        else:
            self.action_button.configure(text=label, state="normal")
            self.action_hint_label.configure(
                text=self.tr("action.hint", "Ctrl+Enter — быстрый запуск")
            )

    def _on_section_changed(self) -> None:
        """Смена раздела: шапка, видимость списка файлов и панели действий."""
        title = self.tabs.get()
        key = self._section_by_title.get(title, "")
        self.section_title.configure(text=title)
        self.section_hint.configure(text=SECTION_HINTS.get(key, ""))

        if key in FILE_SECTIONS:
            self.files_card.grid()
        else:
            self.files_card.grid_remove()

        if key in ("compress", "convert", "trim"):
            self.action_card.grid()
        else:
            self.action_card.grid_remove()

        self._sync_action_bar()
        self._sync_estimate()

    def _on_action_button(self) -> None:
        if len(self.file_list.checked_indices()) >= 2:
            self.batch_apply()
        else:
            self.start_current_tab()

    def _build_progress(self) -> None:
        self.progress_card = Card(self.content, pady=12)
        self.progress_card.grid(row=6, column=0, sticky="ew", padx=PAGE_PAD, pady=(0, GAP))
        self.progress_panel = ProgressPanel(self.progress_card.body, on_cancel=self.cancel_active)
        self.progress_panel.grid(row=0, column=0, sticky="ew")
        self.progress_card.grid_remove()

    def _show_progress(self, visible: bool) -> None:
        if visible:
            self.progress_card.grid()
        else:
            self.progress_card.grid_remove()

    def _build_status(self) -> None:
        self.status_strip = StatusStrip(self, STATUS_CHIPS)
        self.status_strip.grid(row=1, column=0, sticky="ew")
        self._set_status(self.tr("status.ready", "Готово"))
        self.status_strip.show("ffmpeg", "idle", "Поиск FFmpeg…")
        self.status_strip.show("gpu", "idle", "Определение видеоускорителя…")
        self.status_strip.show("job", "idle", "Заданий не выполняется")
        self._update_queue_chip()

    def _set_status(self, text: str, severity: str = "muted") -> None:
        """Сообщение в строке состояния — то же место для всех уведомлений."""
        self.status_strip.show_message(text, severity)

    def _update_queue_chip(self) -> None:
        jobs = self.app.queue.jobs
        pending = [job for job in jobs if job.is_active]
        health = "busy" if pending else ("ok" if jobs else "idle")
        self.status_strip.show(
            "queue",
            health,
            f"Всего заданий: {len(jobs)}, выполняется: {len(pending)}",
            f"Очередь: {len(jobs)}",
        )

    # -- события приложения --------------------------------------------------
    def _subscribe(self) -> None:
        bus = self.app.bus
        bus.subscribe(Event.FILES_CHANGED, lambda files: self.after(0, self._files_changed, files))
        bus.subscribe(Event.IMPORT_STARTED, lambda count: self.after(0, self._import_started, count))
        bus.subscribe(
            Event.IMPORT_FINISHED, lambda added, failed: self.after(0, self._import_finished, added, failed)
        )
        bus.subscribe(Event.CAPABILITIES_READY, lambda caps: self.after(0, self._capabilities_ready, caps))
        bus.subscribe(Event.GPU_DETECTED, lambda gpus: self.after(0, self._gpu_detected, gpus))
        bus.subscribe(Event.FFMPEG_MISSING, lambda: self.after(0, self._ffmpeg_missing))
        bus.subscribe(Event.WATCH_FILES, lambda paths: self.after(0, self._watch_files, paths))
        bus.subscribe(Event.JOB_ADDED, lambda job: self.after(0, self._job_added, job))
        bus.subscribe(Event.JOB_STARTED, lambda job: self.after(0, self._job_started, job))
        bus.subscribe(
            Event.JOB_PROGRESS, lambda job, info: self.after(0, self._job_progress, job, info)
        )
        bus.subscribe(Event.JOB_FINISHED, lambda job: self.after(0, self._job_finished, job))
        bus.subscribe(Event.QUEUE_CHANGED, lambda: self.after(0, self._queue_changed))
        bus.subscribe(Event.QUEUE_IDLE, lambda: self.after(0, self._queue_idle))
        bus.subscribe(Event.UPDATE_CHECKED, lambda state: self.after(0, self._update_checked, state))

    def _bind_shortcuts(self) -> None:
        """Раздел 51."""
        self.bind("<Control-o>", lambda _e: self.add_files())
        self.bind("<Control-O>", lambda _e: self.add_files())
        self.bind("<Control-Shift-O>", lambda _e: self.add_folder())
        self.bind("<Delete>", lambda _e: self.remove_selected())
        self.bind("<Control-Return>", lambda _e: self.start_current_tab())
        self.bind("<Escape>", lambda _e: self.cancel_active())
        self.bind("<Control-s>", lambda _e: self.save_project())
        self.bind("<Control-l>", lambda _e: self.show_logs())
        self.bind("<F1>", lambda _e: self.show_help())

    # -- импорт -------------------------------------------------------------
    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title=self.tr("import.add_file", "Добавить файл"))
        if paths:
            self.app.import_paths(list(paths))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title=self.tr("import.add_folder", "Добавить папку"))
        if folder:
            self.app.import_paths([folder])

    def on_drop(self, paths: list[str]) -> None:
        self.app.import_paths(paths)

    def clear_files(self) -> None:
        self.app.clear_files()

    def remove_selected(self) -> None:
        if self.app.state.selected_index >= 0:
            self.app.remove_file(self.app.state.selected_index)

    def select_file(self, index: int) -> None:
        media = self.app.select_file(index)
        self.file_list.highlight(index)
        for tab in self.operation_tabs:
            tab.set_file(media)

    # -- обработчики событий -------------------------------------------------
    def _files_changed(self, files: list[MediaFile]) -> None:
        self.file_list.set_files(files, self.app.state.selected_index)
        if files and self.app.state.selected_index < 0:
            self.select_file(0)
        elif files:
            self.select_file(self.app.state.selected_index)
        else:
            for tab in self.operation_tabs:
                tab.set_file(None)

    def _import_started(self, count: int) -> None:
        self._set_status(f"{self.tr('status.probing', 'Чтение информации о файлах…')} ({count})")

    def _import_finished(self, added: int, failed: int) -> None:
        message = self.tr("import.done", "Импортировано файлов: {added}").format(added=added)
        if failed:
            message += "   •   " + self.tr(
                "import.failed", "Не распознано файлов: {failed}"
            ).format(failed=failed)
        self._set_status(message, "warning" if failed else "muted")

    def _capabilities_ready(self, capabilities) -> None:
        for tab in (self.compress_tab, self.convert_tab, self.trim_tab):
            tab.on_capabilities_ready()
        self.settings_tab.update_ffmpeg_status()
        self.settings_tab.show_encoder_probe()
        self._set_status(
            f"FFmpeg готов   •   энкодеров: {len(capabilities.encoders)}   •   "
            f"контейнеров: {len(capabilities.muxers)}   •   "
            f"аппаратное ускорение: {', '.join(capabilities.hwaccels) or '—'}"
        )
        binaries = self.app.ffmpeg.binaries
        self.status_strip.show(
            "ffmpeg",
            "ok",
            f"FFmpeg {binaries.ffmpeg_version}\n{binaries.ffmpeg}\n"
            f"Энкодеров: {len(capabilities.encoders)}, контейнеров: {len(capabilities.muxers)}",
        )
        if not DND_AVAILABLE:
            self.import_hint.configure(text="Drag & Drop недоступен (нет tkinterdnd2)")

    def _gpu_detected(self, gpus) -> None:
        from ..services.gpu_detector import summarize

        summary = summarize(gpus)
        known = [gpu for gpu in gpus if gpu.vendor != "UNKNOWN"]
        self.status_strip.show(
            "gpu",
            "ok" if known else "idle",
            summary,
            known[0].vendor if known else "GPU",
        )
        # Видеокарта определяется в отдельном потоке и обычно позже, чем
        # строятся списки кодеков: без пересборки в них остались бы
        # аппаратные варианты, которых это железо не потянет.
        self._refresh_codec_lists()

    def _ffmpeg_missing(self) -> None:
        """Раздел 46."""
        self.status_strip.show("ffmpeg", "error", "FFmpeg не найден")
        # Указанный вручную путь может снова оказаться неверным, а это тот же
        # самый сигнал — без защиты диалог открывался бы поверх себя.
        if self._ffmpeg_dialog_open:
            return
        self._ffmpeg_dialog_open = True
        try:
            self._ask_for_ffmpeg()
        finally:
            self._ffmpeg_dialog_open = False
        self.settings_tab.update_ffmpeg_status()

    def _ask_for_ffmpeg(self) -> None:
        answer = messagebox.askyesno(
            "FFmpeg не найден",
            "FFmpeg не найден.\n\n"
            "Нажмите «Да», чтобы указать путь вручную, "
            "или «Нет», чтобы попробовать найти автоматически.\n\n"
            "Автоматическая загрузка FFmpeg из сети не выполняется.",
        )
        if answer:
            path = filedialog.askopenfilename(title="Укажите путь к ffmpeg")
            if path:
                directory = Path(path).parent
                self.app.set_ffmpeg_paths(path, str(directory))
        else:
            binaries = self.app.ffmpeg.locate()
            if binaries.available:
                self.app.set_ffmpeg_paths(str(binaries.ffmpeg), str(binaries.ffprobe))
            else:
                self._set_status(self.tr("error.ffmpeg_missing", "FFmpeg не найден."), "error")

    def _job_added(self, job: Job) -> None:
        del job
        self.queue_tab.refresh_queue()
        self._update_queue_chip()

    def _queue_changed(self) -> None:
        self.queue_tab.refresh_queue()
        self._update_queue_chip()

    def _job_started(self, job: Job) -> None:
        self._active_job = job
        self._show_progress(True)
        self.progress_panel.start(f"{self.tr('status.encoding', 'Кодирование')}: {job.label}")
        self.status_strip.show("job", "busy", f"Выполняется: {job.label}")
        self.queue_tab.update_job(job)
        self._update_queue_chip()

    def _job_progress(self, job: Job, info: ProgressInfo) -> None:
        total = job.source.duration if job.source else None
        if job.trim.enabled:
            total = job.trim.effective_duration() or total
        self.progress_panel.update_progress(info, total)
        self.queue_tab.update_job(job, info)

    def _job_finished(self, job: Job) -> None:
        self.queue_tab.update_job(job)
        self.queue_tab.refresh_queue()
        if job.status == JobStatus.COMPLETED.value:
            details = job.stats.report() if job.stats else ""
            self.progress_panel.finish(f"{self.tr('status.completed', 'Завершено')}: {job.label}")
            self.status_strip.show("job", "ok", f"Завершено: {job.label}")
            self._set_status(
                f"{job.label} → {Path(job.output_file).name}"
                + (f"   •   экономия {job.stats.saved_percent:.1f}%" if job.stats else ""),
                "ok",
            )
            if details:
                self._show_result(job, details)
        elif job.status == JobStatus.FAILED.value:
            self.progress_panel.reset(self.tr("status.failed", "Ошибка"))
            self.status_strip.show("job", "error", f"Ошибка: {job.label}")
            self._set_status(f"{self.tr('status.failed', 'Ошибка')}: {job.label}", "error")
            self._show_error(job)
        elif job.status == JobStatus.CANCELLED.value:
            self.progress_panel.reset(self.tr("status.cancelled", "Отменено"))
            self.status_strip.show("job", "warning", f"Отменено: {job.label}")
        self._active_job = None
        self._update_queue_chip()

    def _queue_idle(self) -> None:
        self.progress_panel.reset()
        self._show_progress(False)
        self._set_status(self.tr("status.ready", "Готово"))
        self._update_queue_chip()

    # -- обновления ------------------------------------------------------------
    def _update_checked(self, state) -> None:
        """Ответ проверки обновлений: кнопка в сайдбаре и строка состояния."""
        self.settings_tab.show_update_state(state)
        if state.error or not state.available:
            self.update_button.grid_remove()
            return
        self.update_button.configure(text=f"Обновление  ·  v{state.latest.version}")
        self.update_button.grid()
        self._set_status(f"Доступно обновление: v{state.latest.version}", "info")

    def show_updates(self, only_new: bool = True) -> None:
        """Окно «Доступно обновление». ``only_new`` — без истории прошлых версий."""
        state = self.app.update_state
        releases = state.newer if only_new and state.newer else state.releases
        if not releases:
            self._set_status(
                state.error or "Обновлений не найдено — установлена последняя версия.",
                "error" if state.error else "muted",
            )
            return
        window = UpdateWindow(
            self,
            releases,
            self.app.updates,
            title="Доступно обновление" if state.available else "История изменений",
            on_history=(lambda: self.show_updates(only_new=False)) if only_new else None,
        )
        window.after(200, window.focus)

    def on_settings_changed(self) -> None:
        theming.set_appearance_mode(self.app.settings.theme)
        self.compress_tab.video_panel.apply_default_crf(self.app.settings.default_crf)
        self.convert_tab.video_panel.apply_default_crf(self.app.settings.default_crf)
        self.trim_tab.video_panel.apply_default_crf(self.app.settings.default_crf)
        # Раздел «Кодеки и контейнеры»: базовый/расширенный список
        # переключается мгновенно, без переоткрытия разделов.
        self._refresh_codec_lists()
        self.convert_tab.refresh_container_choices()

    def _refresh_codec_lists(self) -> None:
        """Пересобирает списки кодеков во всех разделах, работающих с файлами."""
        for tab in (self.compress_tab, self.convert_tab, self.trim_tab):
            tab.video_panel.refresh_encoders()
            tab.audio_panel.refresh_encoders()

    # -- запуск заданий ------------------------------------------------------
    def start_current_tab(self) -> None:
        tab = self._active_job_tab()
        if tab is not None:
            tab.start()

    def batch_apply(self) -> None:
        """Раздел «Пакетная обработка»: настройки текущего раздела
        применяются к каждому отмеченному флажком файлу, и все задания
        сразу ставятся в очередь."""
        checked = self.file_list.checked_indices()
        if len(checked) < 2:
            return
        tab = self._active_job_tab()
        if tab is None:
            return

        files = self.app.state.files
        jobs: list[Job] = []
        for index in checked:
            if not 0 <= index < len(files):
                continue
            job = tab.build_job_for(files[index])
            if job is not None:
                jobs.append(job)
        if not jobs:
            return

        confirmed = [job for job in jobs if self._confirm_output(job)]
        if not confirmed:
            return
        for job in confirmed:
            self.app.enqueue(job, autostart=False)
        self.app.queue.start()
        self.queue_tab.refresh_queue()
        self._set_status(f"В очередь добавлено файлов: {len(confirmed)} из {len(jobs)}")
        self.file_list.check_all(False)
        self._select_all_var.set(False)
        self._sync_action_bar()

    def enqueue(self, job: Job) -> None:
        """Общий обработчик кнопок запуска на разделах операций."""
        if not self._confirm_output(job):
            return
        self.app.enqueue(job)
        self.queue_tab.refresh_queue()
        self._set_status(f"Задание добавлено в очередь: {job.label}")

    def _confirm_output(self, job: Job) -> bool:
        """Раздел 31: без разрешения файл не перезаписывается."""
        output = job.output_path
        if not output.exists():
            return True
        policy = self.app.settings.overwrite_policy
        if policy == OverwritePolicy.OVERWRITE.value:
            job.overwrite_policy = OverwritePolicy.OVERWRITE.value
            return True
        if policy == OverwritePolicy.RENAME.value:
            job.overwrite_policy = OverwritePolicy.RENAME.value
            return True

        dialog = messagebox.askyesnocancel(
            "Файл существует",
            f"Файл {output.name} уже существует.\n\n"
            "Да — перезаписать\n"
            f"Нет — создать {self.app.filesystem.unique_path(output).name}\n"
            "Отмена — не выполнять",
        )
        if dialog is None:
            return False
        job.overwrite_policy = (
            OverwritePolicy.OVERWRITE.value if dialog else OverwritePolicy.RENAME.value
        )
        return True

    def cancel_active(self) -> None:
        job = self._active_job
        if job is not None:
            self.app.queue.cancel(job.id)
            self._set_status(f"Отмена: {job.label}", "warning")

    # -- диалоги -------------------------------------------------------------
    def _dialog(self, title: str, size: str) -> ctk.CTkToplevel:
        """Окно диалога в оформлении приложения: фон окна и общая сетка."""
        window = ctk.CTkToplevel(self)
        window.title(title)
        window.geometry(size)
        window.transient(self)
        window.configure(fg_color=pair("bg.window"))
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)
        return window

    def _show_error(self, job: Job) -> None:
        """Раздел 27."""
        window = self._dialog(self.tr("error.title", "Ошибка"), "720x480")

        card = Card(window, title=self.tr("error.title", "Ошибка"), subtitle=job.label)
        card.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        card.body.grid_rowconfigure(0, weight=1)

        textbox = ctk.CTkTextbox(card.body, wrap="word", font=font("mono"))
        textbox.grid(row=0, column=0, sticky="nsew")
        textbox.insert("1.0", job.error or "Неизвестная ошибка")
        textbox.configure(state="disabled")

        buttons = ctk.CTkFrame(window, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=PAGE_PAD)
        buttons.grid_columnconfigure(2, weight=1)
        button(
            buttons,
            self.tr("error.show_log", "Показать технический лог"),
            lambda: self.queue_tab.open_log(job),
        ).grid(row=0, column=0)
        button(
            buttons,
            self.tr("error.copy", "Копировать"),
            lambda: self.queue_tab.copy_to_clipboard(job.error or ""),
        ).grid(row=0, column=1, padx=8)
        button(
            buttons, self.tr("common.close", "Закрыть"), window.destroy, variant="primary"
        ).grid(row=0, column=3, sticky="e")

    def _show_result(self, job: Job, details: str) -> None:
        """Раздел 53."""
        window = self._dialog(self.tr("result.title", "Результат"), "480x560")

        card = Card(window, title=self.tr("result.title", "Готово"), subtitle=job.label)
        card.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        card.body.grid_rowconfigure(0, weight=1)

        textbox = ctk.CTkTextbox(card.body, wrap="word", font=font("small"))
        textbox.grid(row=0, column=0, sticky="nsew")
        textbox.insert("1.0", details)
        textbox.configure(state="disabled")

        buttons = ctk.CTkFrame(window, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=PAGE_PAD)
        buttons.grid_columnconfigure(1, weight=1)
        from .tabs.queue_tab import open_folder

        button(
            buttons,
            self.tr("result.open_folder", "Открыть папку"),
            lambda: open_folder(Path(job.output_file)),
        ).grid(row=0, column=0)
        button(
            buttons, self.tr("common.close", "Закрыть"), window.destroy, variant="primary"
        ).grid(row=0, column=2, sticky="e")

    def show_logs(self) -> None:
        """Ctrl+L — общий лог приложения."""
        path = self.app.store.logs_dir / "ffmpeg.log"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-200000:]
        except OSError as exc:
            text = f"Лог недоступен: {exc}"
        window = self._dialog("Лог FFmpeg", "980x640")

        card = Card(window, title="Лог FFmpeg", subtitle=str(path))
        card.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(PAGE_PAD, 0))
        card.body.grid_rowconfigure(0, weight=1)

        textbox = ctk.CTkTextbox(card.body, wrap="none", font=font("mono"))
        textbox.grid(row=0, column=0, sticky="nsew")
        textbox.insert("1.0", text)
        textbox.configure(state="disabled")

        button(
            window, self.tr("common.close", "Закрыть"), window.destroy, variant="primary"
        ).grid(row=1, column=0, pady=PAGE_PAD)

    def show_help(self) -> None:
        """F1 — краткая справка и горячие клавиши."""
        window = self._dialog(self.tr("app.help", "Справка"), "640x740")
        # Растягивается только пустое место под карточками: сами карточки
        # должны показывать все строки, а не обрезать последнюю.
        window.grid_rowconfigure(0, weight=0)
        window.grid_rowconfigure(1, weight=0)
        window.grid_rowconfigure(2, weight=1)

        steps = Card(window, title="Порядок работы")
        steps.grid(row=0, column=0, sticky="ew", padx=PAGE_PAD, pady=(PAGE_PAD, GAP))
        for index, text in enumerate(
            (
                "Добавьте файлы кнопкой или перетаскиванием.",
                "Выберите файл в списке — FFprobe покажет сведения.",
                "Откройте раздел операции и настройте параметры.",
                "Проверьте команду кнопкой «Показать команду FFmpeg».",
                "Запустите — задание попадёт в очередь.",
            )
        ):
            ctk.CTkLabel(
                steps.body,
                text=f"{index + 1}.  {text}",
                anchor="w",
                justify="left",
                font=font("body"),
            ).grid(row=index, column=0, sticky="ew", pady=1)

        shortcuts = Card(window, title="Горячие клавиши")
        shortcuts.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=(0, GAP))
        shortcuts.label_grid()
        for index, (keys, description) in enumerate(
            (
                ("Ctrl+O", "Добавить файл"),
                ("Ctrl+Shift+O", "Добавить папку"),
                ("Delete", "Удалить выбранный файл"),
                ("Ctrl+Enter", "Запустить"),
                ("Esc", "Отменить текущее задание"),
                ("Ctrl+S", "Сохранить проект"),
                ("Ctrl+L", "Лог FFmpeg"),
                ("F1", "Справка"),
            )
        ):
            ctk.CTkLabel(
                shortcuts.body, text=keys, anchor="w", width=130, font=font("mono")
            ).grid(row=index, column=0, sticky="w", pady=2)
            muted(shortcuts.body, description).grid(row=index, column=1, sticky="w", pady=2)

        muted(
            window,
            "Значок ⓘ рядом с параметром открывает расширенную справку.",
        ).grid(row=2, column=0, sticky="w", padx=PAGE_PAD + 4)

        button(
            window, self.tr("common.close", "Закрыть"), window.destroy, variant="primary"
        ).grid(row=3, column=0, pady=PAGE_PAD)

    def save_project(self) -> None:
        """Ctrl+S — сохранение списка файлов и текущих настроек."""
        path = filedialog.asksaveasfilename(
            title="Сохранить проект", defaultextension=".ffgui", initialfile="project.ffgui"
        )
        if not path:
            return
        import json

        data = {
            "version": 1,
            "files": [str(media.path) for media in self.app.state.files],
            "settings": self.app.settings.to_dict(),
        }
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._set_status(f"Проект сохранён: {Path(path).name}", "ok")
        except OSError as exc:
            self._set_status(f"Не удалось сохранить проект: {exc}", "error")

    # -- завершение ----------------------------------------------------------
    def on_close(self) -> None:
        if self.app.queue.active_count and not messagebox.askyesno(
            "Выход", "Есть выполняющиеся задания. Прервать и выйти?"
        ):
            return
        self.app.shutdown()
        self.destroy()
