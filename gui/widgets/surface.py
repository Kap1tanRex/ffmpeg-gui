"""Базовые элементы оформления: карточка, подписи, кнопки.

Тот же набор примитивов, что и в ZapretGUI (``presentation/views/widgets.py``):
страница собирается из карточек, у карточки есть заголовок и тело, а кнопки
различаются не цветом «на глаз», а вариантом (``primary``, ``danger``,
``ghost``, ``link``), за которым закреплены токены темы.

Все цвета берутся только из :mod:`gui.theming`; ни один литерал ``#RRGGBB``
в виджетах приложения больше не встречается.
"""

from __future__ import annotations

import tkinter
from collections.abc import Callable

import customtkinter as ctk

from ..theming import color, font, pair, radius

#: Отступы внутри карточки и между карточками (сетка 4 px, как в ZapretGUI).
CARD_PADX = 20
CARD_PADY = 18
GAP = 14
PAGE_PAD = 18


class Card(ctk.CTkFrame):
    """Поверхность с рамкой и необязательным заголовком — блок страницы.

    Содержимое кладётся в :attr:`body`; сама карточка отвечает только за фон,
    рамку, скругление и отступы, поэтому вложенные виджеты не знают о теме.
    """

    def __init__(
        self,
        master,
        title: str = "",
        subtitle: str = "",
        *,
        padx: int = CARD_PADX,
        pady: int = CARD_PADY,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            corner_radius=radius("md"),
            fg_color=pair("bg.surface"),
            border_width=1,
            border_color=pair("border"),
            **kwargs,
        )
        self.grid_columnconfigure(0, weight=1)

        self._row = 0
        self.title_label: ctk.CTkLabel | None = None
        self.subtitle_label: ctk.CTkLabel | None = None

        if title:
            self.title_label = ctk.CTkLabel(
                self, text=title, anchor="w", font=font("large", bold=True)
            )
            self.title_label.grid(row=0, column=0, sticky="ew", padx=padx, pady=(pady, 0))
            self._row = 1
        if subtitle:
            self.subtitle_label = muted(self, subtitle)
            self.subtitle_label.grid(
                row=self._row, column=0, sticky="ew", padx=padx, pady=(2, 0)
            )
            self._row += 1

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(
            row=self._row,
            column=0,
            sticky="nsew",
            padx=padx,
            pady=(GAP if (title or subtitle) else pady, pady),
        )
        self.body.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(self._row, weight=1)

    def set_title(self, text: str) -> None:
        if self.title_label is not None:
            self.title_label.configure(text=text)

    def label_grid(self, control_column: int = 1) -> ctk.CTkFrame:
        """Переводит тело карточки в режим таблицы «подпись — контрол».

        По умолчанию растягивается первая колонка — так одиночный виджет
        занимает всю ширину карточки. Для строк с подписью слева тянуться
        должна колонка контрола, иначе подпись отъезжает от своего поля.
        """
        self.body.grid_columnconfigure(0, weight=0)
        self.body.grid_columnconfigure(control_column, weight=1)
        return self.body


# ── подписи ────────────────────────────────────────────────────────────────────
def muted(master, text: str = "", **kwargs) -> ctk.CTkLabel:
    """Пояснение вторичным цветом — подсказки, значения по умолчанию, единицы.

    Выравнивание и шрифт можно переопределить: значения ниже — умолчания,
    а не жёсткие настройки.
    """
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    kwargs.setdefault("text_color", pair("fg.secondary"))
    kwargs.setdefault("font", font("small"))
    return ctk.CTkLabel(master, text=text, **kwargs)


#: Уровень состояния -> токен цвета.
SEVERITY_TOKENS = {
    "ok": "state.ok",
    "warning": "state.warn",
    "warn": "state.warn",
    "error": "state.error",
    "critical": "state.error",
    "info": "state.info",
    "muted": "fg.secondary",
}


def severity_color(severity: str) -> tuple[str, str]:
    return pair(SEVERITY_TOKENS.get(severity, "fg.secondary"))


def hairline(master, *, vertical: bool = False, token: str = "border") -> tkinter.Frame:
    """Линия толщиной в пиксель.

    Намеренно голый ``tkinter.Frame``: рамка CustomTkinter рисует себя на
    собственном холсте и при высоте в один пиксель не рисует ничего — линия
    просто не появляется на экране.
    """
    line = tkinter.Frame(master, background=color(token), borderwidth=0, highlightthickness=0)
    if vertical:
        line.configure(width=1)
    else:
        line.configure(height=1)
    return line


# ── кнопки ───────────────────────────────────────────────────────────────────
def button(
    master,
    text: str,
    command: Callable[[], None] | None = None,
    *,
    variant: str = "secondary",
    compact: bool = False,
    **kwargs,
) -> ctk.CTkButton:
    """Кнопка одного из вариантов оформления.

    ``primary``   — акцентная, главное действие экрана;
    ``secondary`` — поверхность с рамкой (по умолчанию);
    ``danger``    — необратимое действие, текст цветом ошибки;
    ``ghost``     — без рамки и фона, для панелей инструментов;
    ``link``      — текст акцентным цветом, выглядит как ссылка.
    """
    style: dict = {
        "corner_radius": radius("sm"),
        "height": 26 if compact else 32,
        "font": font("small" if compact else "body"),
    }
    if variant == "primary":
        style.update(
            fg_color=pair("accent"),
            hover_color=pair("accent.hover"),
            text_color=pair("accent.fg"),
            border_width=0,
            font=font("small" if compact else "body", bold=True),
        )
    elif variant == "danger":
        style.update(
            fg_color=pair("bg.elevated"),
            hover_color=pair("bg.hover"),
            border_width=1,
            border_color=pair("border.strong"),
            text_color=pair("state.error"),
        )
    elif variant == "ghost":
        style.update(
            fg_color="transparent",
            hover_color=pair("bg.hover"),
            border_width=0,
            text_color=pair("fg.secondary"),
        )
    elif variant == "link":
        style.update(
            fg_color="transparent",
            hover_color=pair("bg.hover"),
            border_width=0,
            text_color=pair("accent"),
        )
    style.update(kwargs)
    return ctk.CTkButton(master, text=text, command=command, **style)
