"""Панель прогресса текущего задания (раздел 26).

Показывает три вещи в порядке важности: что кодируется, сколько процентов
готово и подробности (скорость, битрейт, оставшееся время). Пока задание не
запущено, панель ничего не занимает — кнопка отмены и подпись скрыты.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from ...core.models import ProgressInfo
from ..theming import font, pair
from .surface import button


class ProgressPanel(ctk.CTkFrame):
    def __init__(self, master, on_cancel: Callable[[], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self.on_cancel = on_cancel
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(header, text="", anchor="w", font=font("body", bold=True))
        self.title_label.grid(row=0, column=0, sticky="ew")

        self.percent_label = ctk.CTkLabel(
            header, text="", anchor="e", font=font("small"), text_color=pair("fg.secondary")
        )
        self.percent_label.grid(row=0, column=1, sticky="e", padx=(8, 0))

        self.bar = ctk.CTkProgressBar(self, height=8)
        self.bar.set(0)
        self.bar.grid(row=1, column=0, sticky="ew", pady=(6, 4), padx=(0, 8))

        self.detail_label = ctk.CTkLabel(
            self,
            text="",
            anchor="w",
            justify="left",
            font=font("small"),
            text_color=pair("fg.secondary"),
        )
        self.detail_label.grid(row=2, column=0, sticky="ew")

        self.cancel_button = button(
            self, "Отмена", self._on_cancel, variant="danger", compact=True, width=90
        )
        self.cancel_button.grid(row=0, column=1, rowspan=3, padx=(12, 0))
        self.cancel_button.grid_remove()

    def _on_cancel(self) -> None:
        self.on_cancel()

    def start(self, text: str) -> None:
        self.title_label.configure(text=text)
        self.detail_label.configure(text="")
        self.percent_label.configure(text="0 %")
        self.bar.set(0)
        self.cancel_button.grid()

    def update_progress(self, info: ProgressInfo, total: float | None) -> None:
        fraction = max(0.0, min(1.0, info.fraction))
        self.bar.set(fraction)
        self.percent_label.configure(text=f"{fraction * 100:.0f} %")
        self.detail_label.configure(text=info.human(total).replace("\n", "   •   "))

    def finish(self, text: str) -> None:
        self.bar.set(1.0)
        self.title_label.configure(text=text)
        self.percent_label.configure(text="100 %")
        self.detail_label.configure(text="")
        self.cancel_button.grid_remove()

    def reset(self, text: str | None = None) -> None:
        self.bar.set(0)
        self.title_label.configure(text=text or "")
        self.percent_label.configure(text="")
        self.detail_label.configure(text="")
        self.cancel_button.grid_remove()
