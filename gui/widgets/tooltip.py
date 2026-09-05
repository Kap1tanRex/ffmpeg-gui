"""Всплывающая подсказка при наведении на параметр (раздел 32).

Показ подсказки проверяет настройку ``show_tooltips`` в момент наведения
(не при создании виджета) — переключатель в настройках работает сразу,
без перезапуска приложения.

Само окно подсказки — общее на всё приложение (:mod:`gui.widgets.hint`):
создание отдельного окна на каждое наведение оставляло их висеть на экране.
"""

from __future__ import annotations

import tkinter

from . import hint


class Tooltip:
    """Подсказка поверх произвольного виджета, с задержкой перед показом."""

    def __init__(self, widget, app, text, delay: int = 450) -> None:
        self.widget = widget
        self.app = app
        #: Строка или список ``(роль, текст)`` — см. :func:`gui.widgets.hint.show`.
        self.text = text
        self.delay = delay
        self._after_id: str | None = None

        #: Нажата ли кнопка мыши: пока пользователь тянет ползунок, подсказка
        #: не всплывает, даже если он замер на середине движения.
        self._pressed = False

        widget.bind("<Enter>", self._schedule, add="+")
        # Подсказка не должна ехать за курсором: пока мышь в движении, окно
        # спрятано, и появляется оно только когда курсор остановился.
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._on_press, add="+")
        widget.bind("<ButtonRelease>", self._on_release, add="+")
        # Уничтоженный виджет не пришлёт <Leave>: подсказка осталась бы на
        # экране, если её показали прямо перед пересборкой списка.
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if not getattr(self.app.settings, "show_tooltips", True) or not self.text:
            return
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except (tkinter.TclError, ValueError):
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        try:
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height()
        except tkinter.TclError:  # pragma: no cover - виджет уже уничтожен
            return
        hint.show(self.widget, self.text, x, y)

    def _on_motion(self, _event=None) -> None:
        """Курсор поехал — прячем и отсчитываем паузу заново."""
        hint.hide()
        if not self._pressed:
            self._schedule()

    def _on_press(self, _event=None) -> None:
        """Пользователь взялся за параметр — подсказка мешает и уходит."""
        self._pressed = True
        self._hide()

    def _on_release(self, _event=None) -> None:
        self._pressed = False
        self._schedule()

    def _hide(self, _event=None) -> None:
        self._cancel()
        hint.hide()


def attach_help(widget, app, key: str) -> Tooltip | None:
    """Привязывает подсказку из :mod:`core.help_registry` по ключу параметра."""
    from ...core.help_registry import registry

    entry = registry.get(key)
    if entry is None:
        return None
    return Tooltip(widget, app, entry.sections())
