"""Панель обработки картинки: поворот, кадрирование, шум, водяной знак.

Вынесена из ``_controls.py``, который и без того отвечает за две панели.
Панель отдаёт :class:`core.filters.FilterOptions` и параметры наложения;
превращать их в строки фильтров — дело :mod:`core.filters` и сборщика команд.
"""

from __future__ import annotations

from collections.abc import Callable
from tkinter import filedialog

import customtkinter as ctk

from ...core.filters import DENOISE, POSITIONS, ROTATIONS, FilterOptions
from ..theming import font, pair
from ..widgets.surface import Card, button, muted, severity_color
from ..widgets.tooltip import attach_help
from ._controls import _caption


class FilterPanel(Card):
    """Что сделать с картинкой до кодирования."""

    def __init__(self, master, app) -> None:
        super().__init__(master, title="Обработка картинки")
        self.app = app
        body = self.label_grid()
        body.grid_columnconfigure(1, weight=1)
        self.on_user_change: "Callable[[], None] | None" = None
        self.on_changed: "Callable[[], None] | None" = None

        row = 0
        _caption(body, "Поворот", row)
        self.rotate_menu = ctk.CTkOptionMenu(
            body,
            values=list(ROTATIONS),
            command=lambda _v: self._notify_user_change(),
            width=220,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.rotate_menu.set(next(iter(ROTATIONS)))
        self.rotate_menu.grid(row=row, column=1, sticky="w", pady=3)
        attach_help(self.rotate_menu, app, "rotate")
        row += 1

        _caption(body, "Кадрирование", row)
        crop_frame = ctk.CTkFrame(body, fg_color="transparent")
        crop_frame.grid(row=row, column=1, sticky="w", pady=3)
        self.crop_entries: dict[str, ctk.CTkEntry] = {}
        for column, (key, placeholder) in enumerate(
            (("width", "ширина"), ("height", "высота"), ("x", "X"), ("y", "Y"))
        ):
            entry = ctk.CTkEntry(
                crop_frame,
                width=68 if column < 2 else 50,
                placeholder_text=placeholder,
                font=font("small"),
            )
            entry.grid(row=0, column=column, padx=(0, 6))
            entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
            self.crop_entries[key] = entry
        attach_help(crop_frame, app, "crop")
        row += 1

        _caption(body, "Улучшение", row)
        checks = ctk.CTkFrame(body, fg_color="transparent")
        checks.grid(row=row, column=1, sticky="w", pady=3)
        self.deinterlace_check = ctk.CTkCheckBox(
            checks,
            text="убрать гребёнку",
            command=self._notify_user_change,
            font=font("small"),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.deinterlace_check.grid(row=0, column=0, padx=(0, 14))
        attach_help(self.deinterlace_check, app, "deinterlace")
        ctk.CTkLabel(checks, text="шум:", font=font("small")).grid(row=0, column=1, padx=(0, 6))
        self.denoise_menu = ctk.CTkOptionMenu(
            checks,
            values=list(DENOISE),
            command=lambda _v: self._notify_user_change(),
            width=110,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.denoise_menu.set(next(iter(DENOISE)))
        self.denoise_menu.grid(row=0, column=2)
        attach_help(self.denoise_menu, app, "denoise")
        row += 1

        _caption(body, "Водяной знак", row)
        mark_frame = ctk.CTkFrame(body, fg_color="transparent")
        mark_frame.grid(row=row, column=1, sticky="ew", pady=3)
        mark_frame.grid_columnconfigure(0, weight=1)
        self.watermark_entry = ctk.CTkEntry(
            mark_frame, placeholder_text="файл PNG или JPG", font=font("small")
        )
        self.watermark_entry.grid(row=0, column=0, sticky="ew")
        self.watermark_entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
        button(mark_frame, "Обзор", self._browse_watermark, width=80).grid(
            row=0, column=1, padx=(6, 0)
        )
        attach_help(self.watermark_entry, app, "watermark")
        row += 1

        mark_row = ctk.CTkFrame(body, fg_color="transparent")
        mark_row.grid(row=row, column=1, sticky="w", pady=(0, 3))
        self.position_menu = ctk.CTkOptionMenu(
            mark_row,
            values=list(POSITIONS),
            command=lambda _v: self._notify_user_change(),
            width=150,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.position_menu.set(next(iter(POSITIONS)))
        self.position_menu.grid(row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(mark_row, text="прозрачность", font=font("small")).grid(row=0, column=1)
        self.opacity_slider = ctk.CTkSlider(
            mark_row,
            from_=0.1,
            to=1.0,
            number_of_steps=9,
            width=110,
            command=lambda _v: self._notify_changed(),
        )
        self.opacity_slider.set(1.0)
        self.opacity_slider.grid(row=0, column=2, padx=(8, 0))
        row += 1

        self.summary = muted(body, "")
        self.summary.grid(row=row, column=1, sticky="w", pady=(2, 0))
        self._refresh_summary()

    # -- события --------------------------------------------------------------
    def _notify_user_change(self) -> None:
        if self.on_user_change is not None:
            self.on_user_change()
        self._notify_changed()

    def _notify_changed(self) -> None:
        self._refresh_summary()
        if self.on_changed is not None:
            self.on_changed()

    def _browse_watermark(self) -> None:
        path = filedialog.askopenfilename(
            title="Картинка водяного знака",
            filetypes=[("Изображения", "*.png *.jpg *.jpeg *.webp"), ("Все файлы", "*.*")],
        )
        if path:
            self.watermark_entry.delete(0, "end")
            self.watermark_entry.insert(0, path)
            self._notify_user_change()

    def _refresh_summary(self) -> None:
        """Одна строка о том, что сейчас применяется к картинке.

        Важное следствие названо прямо: любой фильтр отменяет копирование
        потока, то есть задание перестаёт быть мгновенным.
        """
        options = self.get_options()
        names: list[str] = []
        if options.rotate:
            names.append("поворот")
        if options.crop_filter():
            names.append("кадрирование")
        if options.deinterlace:
            names.append("деинтерлейс")
        if options.denoise:
            names.append("шумоподавление")
        if self.watermark_path():
            names.append("водяной знак")
        if not names:
            self.summary.configure(text="Фильтры не заданы", text_color=pair("fg.secondary"))
            return
        self.summary.configure(
            text=", ".join(names).capitalize() + " — потребует перекодирования",
            text_color=severity_color("info"),
        )

    # -- значения -------------------------------------------------------------
    def get_options(self) -> FilterOptions:
        def number(key: str) -> int | None:
            text = self.crop_entries[key].get().strip()
            try:
                return int(text) if text else None
            except ValueError:
                return None

        return FilterOptions(
            rotate=ROTATIONS.get(self.rotate_menu.get(), ""),
            crop_width=number("width"),
            crop_height=number("height"),
            crop_x=number("x") or 0,
            crop_y=number("y") or 0,
            deinterlace=bool(self.deinterlace_check.get()),
            denoise=DENOISE.get(self.denoise_menu.get(), ""),
        )

    def watermark_path(self) -> str:
        return self.watermark_entry.get().strip()

    @property
    def active(self) -> bool:
        """Есть ли хоть что-то, из-за чего копирование потока невозможно."""
        return self.get_options().active or bool(self.watermark_path())

    def apply_to(self, job) -> None:
        """Переносит выбранное в задание."""
        job.filters = self.get_options().build() + list(job.filters)
        path = self.watermark_path()
        if path:
            job.overlay_path = path
            job.overlay_position = POSITIONS.get(self.position_menu.get(), "br")
            job.overlay_opacity = round(float(self.opacity_slider.get()), 2)

    def set_from(self, job) -> None:
        """Восстанавливает наложение по заданию — для повторов и профилей."""
        self.watermark_entry.delete(0, "end")
        if job.overlay_path:
            self.watermark_entry.insert(0, job.overlay_path)
            for label, value in POSITIONS.items():
                if value == job.overlay_position:
                    self.position_menu.set(label)
                    break
            self.opacity_slider.set(job.overlay_opacity)
        self._refresh_summary()
