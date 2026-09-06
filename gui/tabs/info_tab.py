"""Раздел «Информация» (раздел 7): полный разбор FFprobe."""

from __future__ import annotations

import json

import customtkinter as ctk

from ...core.i18n import t
from ...core.models import MediaFile, format_bitrate, format_size, format_timecode
from ..theming import font
from ..widgets.surface import GAP, Card, button


class InfoTab(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.media: MediaFile | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.card = Card(self, title=t("Сведения о файле"))
        self.card.grid(row=0, column=0, sticky="nsew", pady=(0, GAP))
        self.card.body.grid_rowconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(self.card.body, wrap="word", font=font("mono"))
        self.textbox.grid(row=0, column=0, sticky="nsew")

        buttons = ctk.CTkFrame(self.card.body, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", pady=(GAP, 0))
        buttons.grid_columnconfigure(2, weight=1)
        button(buttons, t("Копировать как текст"), self._copy_text).grid(row=0, column=0)
        button(buttons, t("Копировать как JSON"), self._copy_json).grid(row=0, column=1, padx=8)
        button(buttons, t("Обновить"), self._reprobe, variant="ghost").grid(
            row=0, column=3, sticky="e"
        )

        self._render(None)

    def set_file(self, media: MediaFile | None) -> None:
        self.media = media
        self._render(media)

    def _reprobe(self) -> None:
        if self.media is not None:
            fresh = self.app.reprobe(self.media)
            self.set_file(fresh)

    def _render(self, media: MediaFile | None) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        if media is None:
            self.textbox.insert("1.0", "Выберите файл в списке выше.")
            self.textbox.configure(state="disabled")
            return

        lines = [
            f"Файл: {media.name}",
            f"Путь: {media.path}",
            f"Контейнер: {media.format_long_name or media.format_name or '—'}",
            f"Длительность: {format_timecode(media.duration, with_ms=True)}",
            f"Размер: {format_size(media.size)}",
            f"Битрейт: {format_bitrate(media.bitrate)}",
        ]
        if media.probe_error:
            lines.append(f"Ошибка FFprobe: {media.probe_error}")
        lines.append("")
        lines.append(f"Потоков: {len(media.streams)}")
        for stream in media.streams:
            lines.append(f"  #{stream.index} [{stream.type.value}] {stream.describe()}")
            if stream.type.value == "video" and stream.is_hdr:
                lines.append(f"      HDR: {stream.hdr_kind}")
            if stream.metadata:
                meta = ", ".join(f"{k}={v}" for k, v in stream.metadata.items())
                lines.append(f"      Метаданные: {meta}")
        if media.metadata:
            lines.append("")
            lines.append("Метаданные контейнера:")
            for key, value in media.metadata.items():
                lines.append(f"  {key}: {value}")

        self.textbox.insert("1.0", "\n".join(lines))
        self.textbox.configure(state="disabled")

    def _copy_text(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.textbox.get("1.0", "end"))

    def _copy_json(self) -> None:
        if self.media is None:
            return
        self.clipboard_clear()
        self.clipboard_append(json.dumps(self.media.raw, ensure_ascii=False, indent=2))
