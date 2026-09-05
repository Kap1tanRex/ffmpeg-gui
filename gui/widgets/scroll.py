"""Прокручиваемая область с объединением событий колеса.

Зачем это нужно. ``CTkScrollableFrame`` прокручивает холст прямо в
обработчике колеса: сколько событий прислала система — столько раз и
двинется содержимое. Windows во время быстрой прокрутки шлёт эти события
чаще, чем успевает завершиться перерисовка, и на экране остаются
недорисованные кадры: часть виджетов уже уехала, часть ещё нет.

Здесь события копятся и применяются одним движением раз в кадр. Быстрый
рывок колеса превращается не в десяток частичных перерисовок, а в
несколько полных: суммарное расстояние то же, промежуточных состояний на
экране не остаётся.

``update_idletasks()`` после каждого шага — вторая половина решения: он
доводит перерисовку до конца прежде, чем будет применён следующий шаг.
"""

from __future__ import annotations

import sys
import tkinter

import customtkinter as ctk

#: Раз в сколько миллисекунд применять накопленное. 16 мс — это 60 кадров
#: в секунду: чаще экран всё равно не обновляется.
FRAME_MS = 16

#: Делитель шага колеса. Взят у CustomTkinter, чтобы скорость прокрутки не
#: изменилась: у холста ``yscrollincrement=1``, то есть 20 пикселей на щелчок.
WHEEL_DIVISOR = 6


class ScrollFrame(ctk.CTkScrollableFrame):
    """``CTkScrollableFrame``, который не рвёт картинку при быстрой прокрутке."""

    def __init__(self, *args, **kwargs) -> None:
        # CustomTkinter отступает от края внутрь на величину скругления рамки
        # (corner_radius + border_width). У прозрачной области это скругление
        # не видно, зато содержимое уезжает вправо, и карточки внутри раздела
        # перестают совпадать по левому краю с карточками снаружи.
        kwargs.setdefault("corner_radius", 0)
        super().__init__(*args, **kwargs)
        self._pending_y = 0
        self._pending_x = 0
        #: Последний запрос от полосы прокрутки ("moveto", "0.42") — она шлёт
        #: их на каждое движение мыши, до сотни раз в секунду.
        self._pending_view: tuple = ()
        self._flush_id: str | None = None
        self._scrollbar.configure(command=self._on_scrollbar)

    # CustomTkinter вешает этот обработчик через bind_all на каждый
    # прокручиваемый блок, а нужный из них выбирает _check_if_valid_scroll.
    def _mouse_wheel_all(self, event) -> None:  # noqa: D102 - переопределение
        try:
            if not self._check_if_valid_scroll(event.widget):
                return
        except (tkinter.TclError, AttributeError):  # pragma: no cover
            return

        step = self._wheel_step(event)
        if not step:
            return

        if self._shift_pressed:
            if self._parent_canvas.xview() == (0.0, 1.0):
                return
            self._pending_x += step
        else:
            if self._parent_canvas.yview() == (0.0, 1.0):
                return
            # Колесо и ползунок в пределах одного кадра — редкость, но
            # выигрывать должно последнее движение, а не то, что записано.
            self._pending_view = ()
            self._pending_y += step

        self._schedule_flush()

    def _on_scrollbar(self, *args) -> None:
        """Запрос от полосы прокрутки: запоминаем и применяем раз в кадр.

        Перетаскивание ползунка идёт мимо колеса и потому мимо накопителя
        выше: без этого каждое движение мыши двигало холст напрямую.
        """
        self._pending_view = args
        self._pending_y = 0
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flush_id is None:
            self._flush_id = self.after(FRAME_MS, self._flush_scroll)

    @staticmethod
    def _wheel_step(event) -> int:
        """Событие колеса -> шаг в единицах прокрутки холста."""
        if sys.platform.startswith("win"):
            return -int(event.delta / WHEEL_DIVISOR)
        if sys.platform == "darwin":
            return -int(event.delta)
        # X11 присылает не delta, а номер кнопки: 4 — вверх, 5 — вниз.
        return -1 if getattr(event, "num", 5) == 4 else 1

    def _flush_scroll(self) -> None:
        """Применяет накопленное одним движением.

        Перерисовку намеренно не форсируем: Tk сливает несколько сдвигов,
        пришедших до следующего простоя, в одну отрисовку. Принудительный
        ``update_idletasks()`` после каждого шага эту склейку ломал — двенадцать
        шагов подряд занимали 44 мс вместо 19 и рисовались по отдельности,
        отчего содержимое и расползалось на глазах.
        """
        self._flush_id = None
        view, self._pending_view = self._pending_view, ()
        dy, self._pending_y = self._pending_y, 0
        dx, self._pending_x = self._pending_x, 0
        try:
            if view:
                if self._orientation == "horizontal":
                    self._parent_canvas.xview(*view)
                else:
                    self._parent_canvas.yview(*view)
            elif dy:
                self._parent_canvas.yview("scroll", dy, "units")
            if dx:
                self._parent_canvas.xview("scroll", dx, "units")
        except tkinter.TclError:  # pragma: no cover - виджет уже уничтожен
            return

    def destroy(self) -> None:
        if self._flush_id is not None:
            try:
                self.after_cancel(self._flush_id)
            except (tkinter.TclError, ValueError):  # pragma: no cover
                pass
            self._flush_id = None
        super().destroy()
