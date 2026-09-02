"""Небольшие диалоги, общие для разделов операций.

Предпросмотр команды открывается из «Сжатия», «Конвертации» и «Обрезки»
одинаково, поэтому окно собирается здесь — в оформлении приложения, а не
голым ``CTkToplevel`` в каждом разделе.
"""

from __future__ import annotations

import customtkinter as ctk

from ..theming import font, pair
from ..widgets.surface import PAGE_PAD, Card, button


def show_command(master, command: str, title: str = "Команда FFmpeg") -> ctk.CTkToplevel:
    """Окно с полной командой FFmpeg и кнопкой «Копировать»."""
    window = ctk.CTkToplevel(master)
    window.title(title)
    window.geometry("820x260")
    window.transient(master.winfo_toplevel())
    window.configure(fg_color=pair("bg.window"))
    window.grid_columnconfigure(0, weight=1)
    window.grid_rowconfigure(0, weight=1)

    card = Card(window, title=title, subtitle="Ровно то, что будет запущено")
    card.grid(row=0, column=0, sticky="nsew", padx=PAGE_PAD, pady=(PAGE_PAD, 0))
    card.body.grid_rowconfigure(0, weight=1)

    textbox = ctk.CTkTextbox(card.body, wrap="word", font=font("mono"))
    textbox.grid(row=0, column=0, sticky="nsew")
    textbox.insert("1.0", command)
    textbox.configure(state="disabled")

    buttons = ctk.CTkFrame(window, fg_color="transparent")
    buttons.grid(row=1, column=0, sticky="ew", padx=PAGE_PAD, pady=PAGE_PAD)
    buttons.grid_columnconfigure(1, weight=1)

    def copy() -> None:
        window.clipboard_clear()
        window.clipboard_append(command)

    button(buttons, "Копировать", copy).grid(row=0, column=0)
    button(buttons, "Закрыть", window.destroy, variant="primary").grid(row=0, column=2, sticky="e")
    return window
