"""Нижняя строка состояния: сообщение слева, индикаторы справа.

Повторяет ``status_strip`` ZapretGUI: на вопрос «всё ли в порядке?» отвечает
взгляд на цветные точки, а подробности — во всплывающей подсказке. Индикаторы
всегда на виду и не зависят от того, какой раздел открыт.

Длинное сообщение слева сокращается многоточием, а не раздвигает окно и не
выталкивает индикаторы за его край.
"""

from __future__ import annotations

import tkinter

import customtkinter as ctk

from ..theming import font, pair
from . import hint

DOT = "●"

#: Состояние -> токен цвета (те же уровни, что и в ZapretGUI).
HEALTH_TOKENS = {
    "ok": "state.ok",
    "warning": "state.warn",
    "error": "state.error",
    "busy": "state.info",
    "idle": "fg.secondary",
}


def health_color(health: str) -> tuple[str, str]:
    return pair(HEALTH_TOKENS.get(health, "fg.secondary"))


class EllipsisLabel(ctk.CTkLabel):
    """Подпись, которая сокращается по ширине, а не растягивает родителя."""

    def __init__(self, master, text: str = "", **kwargs) -> None:
        super().__init__(master, text=text, anchor="w", **kwargs)
        self._full_text = text
        self.bind("<Configure>", lambda _event: self._sync())

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self._sync()

    def full_text(self) -> str:
        return self._full_text

    def _sync(self) -> None:
        width = self.winfo_width()
        if width <= 1:
            super().configure(text=self._full_text)
            return
        try:
            metrics = self.cget("font")
            measure = metrics.measure
        except Exception:  # noqa: BLE001 - шрифт ещё не создан
            super().configure(text=self._full_text)
            return
        text = self._full_text
        if measure(text) <= width:
            super().configure(text=text)
            return
        while text and measure(text + "…") > width:
            text = text[:-1]
        super().configure(text=text + "…" if text else "")


class StatusChip(ctk.CTkFrame):
    """Один индикатор: точка, подпись и подсказка, объясняющая состояние."""

    def __init__(self, master, caption: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        self.health = "idle"
        self._tooltip_text = ""

        self.dot = ctk.CTkLabel(self, text=DOT, width=12, text_color=health_color("idle"), font=font("small"))
        self.dot.grid(row=0, column=0, padx=(8, 5))
        self.caption = ctk.CTkLabel(self, text=caption, anchor="w", font=font("small"))
        self.caption.grid(row=0, column=1, padx=(0, 8))

        # Подсказку показывают и дочерние виджеты: переход с рамки на подпись
        # даёт <Leave> у одного и <Enter> у другого, и без общей привязки
        # подсказка мигала бы посреди чипа.
        for widget in (self, self.dot, self.caption):
            widget.bind("<Enter>", self._show_tooltip, add="+")
            widget.bind("<Leave>", self._hide_tooltip, add="+")

    def set_state(self, health: str, tooltip: str = "", caption: str | None = None) -> None:
        self.health = health
        self._tooltip_text = tooltip
        self.dot.configure(text_color=health_color(health))
        if caption is not None:
            self.caption.configure(text=caption)

    # -- подсказка ----------------------------------------------------------
    def _show_tooltip(self, _event=None) -> None:
        if not self._tooltip_text:
            return
        try:
            x = self.winfo_rootx()
            y = self.winfo_rooty()
        except tkinter.TclError:  # pragma: no cover - виджет уже уничтожен
            return
        # Строка состояния прижата к низу окна, поэтому подсказка раскрывается
        # вверх — иначе она уходила бы за край экрана.
        hint.show(self, self._tooltip_text, x, y, above=True)

    def _hide_tooltip(self, _event=None) -> None:
        hint.hide()


class StatusStrip(ctk.CTkFrame):
    """Строка состояния: сообщение слева, цепочка индикаторов справа."""

    def __init__(self, master, chips: tuple[tuple[str, str], ...]) -> None:
        # Заливка внешней рамки видна только полоской сверху — это и есть
        # граница, отделяющая строку состояния от содержимого страницы.
        super().__init__(master, corner_radius=0, fg_color=pair("border"), height=35)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_propagate(False)

        inner = ctk.CTkFrame(self, corner_radius=0, fg_color=pair("bg.surface"))
        inner.grid(row=0, column=0, sticky="nsew", pady=(1, 0))
        inner.grid_columnconfigure(0, weight=1)

        self.message = EllipsisLabel(
            inner, text="", text_color=pair("fg.secondary"), font=font("small")
        )
        self.message.grid(row=0, column=0, sticky="ew", padx=(16, 12), pady=5)

        self.chips: dict[str, StatusChip] = {}
        column = 1
        for index, (chip_id, caption) in enumerate(chips):
            if index:
                # Высота разделителя задаётся явно: пустая рамка CustomTkinter
                # запрашивает 200 px и растянула бы строку состояния.
                ctk.CTkFrame(
                    inner, width=1, height=18, fg_color=pair("border"), corner_radius=0
                ).grid(row=0, column=column, sticky="ns", pady=7)
                column += 1
            chip = StatusChip(inner, caption)
            chip.grid(row=0, column=column, sticky="e")
            self.chips[chip_id] = chip
            column += 1

        hint.prepare(self)
        # Страховка: курсор мог уйти со строки мимо <Leave> отдельного чипа —
        # например, рывком за край окна.
        self.bind("<Leave>", lambda _event: hint.hide(), add="+")

    def show_message(self, text: str, severity: str = "muted") -> None:
        from .surface import severity_color

        self.message.configure(text_color=severity_color(severity) if severity != "muted" else pair("fg.secondary"))
        self.message.set_full_text(text)

    def show(self, chip_id: str, health: str, tooltip: str = "", caption: str | None = None) -> None:
        chip = self.chips.get(chip_id)
        if chip is not None:
            chip.set_state(health, tooltip, caption)

    def health_of(self, chip_id: str) -> str:
        chip = self.chips.get(chip_id)
        return chip.health if chip else "idle"
