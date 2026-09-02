"""Зона перетаскивания файлов (раздел 6).

Работает при установленном ``tkinterdnd2``. Если пакет отсутствует или
инициализация drag & drop не удалась, зона остаётся кликабельной —
файлы можно добавить через диалог (``on_click``).

Оформление — по токенам темы: спокойная поверхность с рамкой, которая
подсвечивается акцентом при наведении, чтобы область читалась как цель для
перетаскивания, а не как ещё одна панель.
"""

from __future__ import annotations

import logging

import customtkinter as ctk

from ..theming import font, pair, radius

log = logging.getLogger(__name__)

try:
    from tkinterdnd2 import DND_FILES

    DND_AVAILABLE = True
except ImportError:  # pragma: no cover
    DND_FILES = None
    DND_AVAILABLE = False


def _split_dnd_paths(data: str) -> list[str]:
    """Разбирает строку Tcl-списка путей от tkinterdnd2 (пути в {} при пробелах)."""
    paths: list[str] = []
    buffer = ""
    in_braces = False
    for char in data:
        if char == "{":
            in_braces = True
            continue
        if char == "}":
            in_braces = False
            continue
        if char == " " and not in_braces:
            if buffer:
                paths.append(buffer)
                buffer = ""
            continue
        buffer += char
    if buffer:
        paths.append(buffer)
    return paths


class DropZone(ctk.CTkFrame):
    """Кликабельная область с поддержкой Drag & Drop."""

    def __init__(self, master, on_drop, on_click, title: str, hint: str, height: int = 72) -> None:
        super().__init__(
            master,
            height=height,
            corner_radius=radius("md"),
            border_width=1,
            border_color=pair("border.strong"),
            fg_color=pair("bg.elevated"),
        )
        self.on_drop = on_drop
        self.on_click = on_click
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=0, column=0)

        self._icon = ctk.CTkLabel(
            self._body, text="⤓", font=ctk.CTkFont(size=22), text_color=pair("accent")
        )
        self._icon.grid(row=0, column=0, rowspan=2, padx=(0, 12))

        self._title = ctk.CTkLabel(self._body, text=title, font=font("body", bold=True))
        self._title.grid(row=0, column=1, sticky="w")

        self._hint = ctk.CTkLabel(
            self._body, text=hint, font=font("small"), text_color=pair("fg.secondary")
        )
        self._hint.grid(row=1, column=1, sticky="w")

        for widget in (self, self._body, self._icon, self._title, self._hint):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")

        self._enable_dnd()

    # -- реакция на курсор ---------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        self.configure(border_color=pair("accent"), fg_color=pair("bg.hover"))

    def _on_leave(self, _event=None) -> None:
        self.configure(border_color=pair("border.strong"), fg_color=pair("bg.elevated"))

    def _on_click(self, _event=None) -> None:
        self.on_click()

    def set_hint(self, text: str) -> None:
        self._hint.configure(text=text)

    def _enable_dnd(self) -> None:
        if not DND_AVAILABLE:
            return
        try:
            self.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - drag&drop опционален
            log.warning("Drag & Drop недоступен: %s", exc)

    def _on_drop(self, event) -> None:
        self._on_leave()
        paths = _split_dnd_paths(event.data)
        if paths:
            self.on_drop(paths)
