"""Боковая навигация со стопкой страниц — замена вкладок сверху.

Повторяет главное окно ZapretGUI: слева фиксированная колонка с названием
приложения и списком разделов, справа — одна видимая страница из стопки.
Разделы читаются вертикальным списком, поэтому их подписи не обрезаются и не
конкурируют за ширину, как ярлыки вкладок.

Наружу выставлен тот же набор методов, что у ``CTkTabview``
(:meth:`add`, :meth:`tab`, :meth:`get`, :meth:`set`, параметр ``command``), —
логика главного окна работает с навигацией без изменений.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from ..theming import font, pair, radius

SIDEBAR_WIDTH = 236


class NavButton(ctk.CTkButton):
    """Пункт бокового меню: активный подсвечивается заливкой, как в ZapretGUI."""

    def __init__(self, master, text: str, command: Callable[[], None]) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            anchor="w",
            height=36,
            corner_radius=radius("sm"),
            fg_color="transparent",
            hover_color=pair("bg.hover"),
            border_width=0,
            text_color=pair("fg.secondary"),
            font=font("body"),
        )
        self._active = False

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.configure(
            fg_color=pair("bg.selected") if active else "transparent",
            text_color=pair("fg.primary") if active else pair("fg.secondary"),
            font=font("body", bold=active),
        )


class NavigationView(ctk.CTkFrame):
    """Сайдбар + стопка страниц с API ``CTkTabview``.

    Область содержимого — сетка: строки до :attr:`PAGE_ROW` и после неё
    остаются главному окну (шапка раздела, общий список файлов, панель
    действий), а сама стопка страниц занимает единственную растягиваемую
    строку между ними.
    """

    #: Строка сетки :attr:`content`, в которой живёт активная страница.
    PAGE_ROW = 4

    def __init__(
        self,
        master,
        *,
        title: str = "",
        subtitle: str = "",
        footer: str = "",
        command: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0, **kwargs)
        self._command = command
        self._pages: dict[str, ctk.CTkFrame] = {}
        self._buttons: dict[str, NavButton] = {}
        self._icons: dict[str, str] = {}
        self._current: str | None = None

        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── сайдбар ──────────────────────────────────────────────────────────
        self.sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=pair("bg.surface")
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        self.sidebar.grid_rowconfigure(3, weight=1)

        self.title_label = ctk.CTkLabel(
            self.sidebar, text=title, anchor="w", font=font("large", bold=True)
        )
        self.title_label.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 0))

        self.subtitle_label = ctk.CTkLabel(
            self.sidebar,
            text=subtitle,
            anchor="w",
            justify="left",
            wraplength=SIDEBAR_WIDTH - 32,
            text_color=pair("fg.secondary"),
            font=font("small"),
        )
        self.subtitle_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 12))

        self.nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_frame.grid(row=2, column=0, sticky="ew", padx=8)
        self.nav_frame.grid_columnconfigure(0, weight=1)

        # Низ сайдбара: место для версии и вспомогательных кнопок.
        self.footer_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.footer_frame.grid(row=4, column=0, sticky="ew", padx=8, pady=(0, 12))
        self.footer_frame.grid_columnconfigure(0, weight=1)

        self._footer_widgets = 0
        self.footer_label = ctk.CTkLabel(
            self.footer_frame,
            text=footer,
            anchor="w",
            text_color=pair("fg.secondary"),
            font=font("small"),
        )
        self.footer_label.grid(row=90, column=0, sticky="ew", padx=8, pady=(8, 0))

        # Вертикальная линия отделяет сайдбар от содержимого.
        from .surface import hairline

        hairline(self, vertical=True).grid(row=0, column=1, sticky="ns")

        # ── стопка страниц ───────────────────────────────────────────────────
        self.content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.content.grid(row=0, column=2, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(self.PAGE_ROW, weight=1)

    # ── API, совместимый с CTkTabview ────────────────────────────────────────
    def add(self, name: str, icon: str = "") -> ctk.CTkFrame:
        if name in self._pages:
            return self._pages[name]

        page = ctk.CTkFrame(self.content, fg_color="transparent", corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self._pages[name] = page
        self._icons[name] = icon

        nav_button = NavButton(self.nav_frame, self._button_text(name), lambda n=name: self.set(n))
        nav_button.grid(row=len(self._buttons), column=0, sticky="ew", pady=2)
        self._buttons[name] = nav_button

        if self._current is None:
            self.set(name)
        return page

    def tab(self, name: str) -> ctk.CTkFrame:
        return self._pages[name]

    def set_command(self, command: Callable[[], None] | None) -> None:
        """Колбэк смены раздела назначается после сборки всех страниц:
        первый ``add`` уже показал начальный раздел, а обработчик тогда ещё
        не мог опираться на не созданные виджеты."""
        self._command = command

    def get(self) -> str:
        return self._current or ""

    def set(self, name: str) -> None:
        if name not in self._pages or name == self._current:
            return
        if self._current is not None:
            self._pages[self._current].grid_forget()
        self._current = name
        self._pages[name].grid(row=self.PAGE_ROW, column=0, sticky="nsew")
        for key, nav_button in self._buttons.items():
            nav_button.set_active(key == name)
        if self._command is not None:
            self._command()

    # ── оформление сайдбара ──────────────────────────────────────────────────
    def _button_text(self, name: str) -> str:
        icon = self._icons.get(name, "")
        return f"  {icon}   {name}" if icon else f"  {name}"

    def set_footer(self, text: str) -> None:
        self.footer_label.configure(text=text)

    def add_footer_widget(self, widget) -> None:
        """Кнопка внизу сайдбара (справка, логи) — над строкой версии."""
        widget.grid(row=self._footer_widgets, column=0, sticky="ew", padx=8, pady=2)
        self._footer_widgets += 1
