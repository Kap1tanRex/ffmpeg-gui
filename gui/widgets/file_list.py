"""Список импортированных файлов (раздел 5).

Флажок слева от каждой строки — пакетный выбор: отметьте несколько файлов,
чтобы применить к ним настройки текущего раздела одной кнопкой (раздел
«Пакетная обработка»). Клик по самой строке — обычный выбор одного файла
для просмотра/настройки, это два независимых механизма.

Строки не пересоздаются. Виджет CustomTkinter стоит около двух миллисекунд —
на списке из двух сотен файлов полная перестройка занимала секунды, а
происходила она при каждом изменении списка, вплоть до удаления одного файла.
Поэтому строки живут в пуле: лишние прячутся, недостающие досоздаются, а
существующим меняется только текст и подсветка.
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from .scroll import ScrollFrame
from ..theming import font, pair, radius


class FileRow:
    """Строка списка: флажок, описание файла и крестик.

    Индекс файла меняется вместе с содержимым, поэтому обработчики читают
    :attr:`index` в момент клика, а не запоминают его при создании.
    """

    def __init__(
        self,
        master,
        on_select: Callable[[int], None],
        on_remove: Callable[[int], None],
        on_check: Callable[[], None],
    ) -> None:
        self.index = -1
        self._on_select = on_select
        self._on_remove = on_remove
        # Что уже показано. Перерисовка рамки CustomTkinter не бесплатна, а
        # список обновляется целиком при любом изменении — трогаем только то,
        # что действительно изменилось.
        self._text = ""
        self._selected: bool | None = None
        self._row = -1

        self.frame = ctk.CTkFrame(
            master,
            fg_color=pair("bg.elevated"),
            corner_radius=radius("sm"),
            border_width=1,
            border_color=pair("border"),
        )
        self.frame.grid_columnconfigure(1, weight=1)

        self.checked = ctk.BooleanVar(value=False)
        self.checkbox = ctk.CTkCheckBox(
            self.frame,
            text="",
            width=20,
            checkbox_width=18,
            checkbox_height=18,
            variable=self.checked,
            command=on_check,
        )
        self.checkbox.grid(row=0, column=0, padx=(10, 4), pady=6)

        self.label = ctk.CTkLabel(self.frame, text="", anchor="w", font=font("small"))
        self.label.grid(row=0, column=1, sticky="ew", padx=4, pady=6)

        # Крестик — подпись, а не кнопка: кнопка CustomTkinter рисует себя на
        # отдельном холсте и стоит вчетверо дороже, а выглядит здесь так же.
        self.remove = ctk.CTkLabel(
            self.frame,
            text="✕",
            width=26,
            corner_radius=radius("sm"),
            text_color=pair("fg.secondary"),
            font=font("small"),
        )
        self.remove.grid(row=0, column=2, padx=(4, 8), pady=4)
        self.remove.bind("<Button-1>", lambda _event: self._on_remove(self.index))
        self.remove.bind("<Enter>", lambda _event: self.remove.configure(fg_color=pair("bg.hover")))
        self.remove.bind("<Leave>", lambda _event: self.remove.configure(fg_color="transparent"))

        for widget in (self.frame, self.label):
            widget.bind("<Button-1>", lambda _event: self._on_select(self.index))

    def show(self, row: int, index: int, media, selected: bool) -> None:
        self.index = index
        text = _describe(media)
        if text != self._text:
            self._text = text
            self.label.configure(text=text)
        if self.checked.get():
            self.checked.set(False)
        self.set_selected(selected)
        if row != self._row:
            self._row = row
            self.frame.grid(row=row, column=0, sticky="ew", pady=2, padx=2)

    def hide(self) -> None:
        if self._row == -1:
            return
        self.index = -1
        self._row = -1
        self.checked.set(False)
        self.frame.grid_remove()

    def set_selected(self, selected: bool) -> None:
        # Выбранную строку отмечает акцентная рамка: отдельная полоска-виджет
        # ради этого не нужна.
        if selected == self._selected:
            return
        self._selected = selected
        self.frame.configure(
            fg_color=pair("bg.selected") if selected else pair("bg.elevated"),
            border_color=pair("accent") if selected else pair("border"),
        )


def _describe(media) -> str:
    if media.probe_error:
        return f"{media.name}   •   ошибка: {media.probe_error}"
    return media.summary_line()


class FileList(ScrollFrame):
    """Прокручиваемый список файлов с выбором, отметками и удалением."""

    def __init__(
        self,
        master,
        on_select: Callable[[int], None],
        on_remove: Callable[[int], None],
        on_check_changed: Callable[[], None] | None = None,
        empty_text: str = "Список файлов пуст",
        height: int = 150,
    ) -> None:
        super().__init__(master, height=height, fg_color="transparent")
        # CustomTkinter задаёт высоту только внутреннему холсту, а внешнюю
        # рамку растягивает менеджер сетки — без этого список занимает 200 px
        # независимо от запрошенной высоты и выдавливает содержимое раздела.
        self._parent_frame.configure(height=height)
        self._parent_frame.grid_propagate(False)
        self.on_select = on_select
        self.on_remove = on_remove
        self.on_check_changed = on_check_changed
        self.grid_columnconfigure(0, weight=1)

        self._rows: list[FileRow] = []
        self._visible = 0

        self._empty_label = ctk.CTkLabel(
            self,
            text=empty_text,
            text_color=pair("fg.secondary"),
            font=font("small"),
            anchor="w",
        )
        self._empty_label.grid(row=0, column=0, sticky="w", padx=10, pady=10)

    # -- содержимое ----------------------------------------------------------
    def set_files(self, files: list, selected_index: int = -1) -> None:
        for position, media in enumerate(files):
            row = self._row_at(position)
            row.show(position, position, media, position == selected_index)
        for row in self._rows[len(files) :]:
            row.hide()
        self._visible = len(files)

        if files:
            self._empty_label.grid_remove()
        else:
            self._empty_label.grid()
        self._notify_check_changed()

    def _row_at(self, position: int) -> FileRow:
        while position >= len(self._rows):
            self._rows.append(
                FileRow(self, self.on_select, self.on_remove, self._notify_check_changed)
            )
        return self._rows[position]

    def _notify_check_changed(self) -> None:
        if self.on_check_changed:
            self.on_check_changed()

    # -- отметки -------------------------------------------------------------
    def checked_indices(self) -> list[int]:
        """Индексы отмеченных флажком файлов (для пакетной обработки)."""
        return [row.index for row in self._rows[: self._visible] if row.checked.get()]

    def check_all(self, value: bool) -> None:
        for row in self._rows[: self._visible]:
            row.checked.set(value)
        self._notify_check_changed()

    # -- выбор ---------------------------------------------------------------
    def highlight(self, index: int) -> None:
        for row in self._rows[: self._visible]:
            row.set_selected(row.index == index)
