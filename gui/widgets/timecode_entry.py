"""Автоформатирование полей ввода тайм-кода (раздел 16 — «Обрезка»).

Пользователь вводит только цифры — каждая новая цифра сдвигает предыдущие
влево, как на секундомере, а поле сразу показывает результат в формате
ЧЧ:ММ:СС. Backspace удаляет последнюю введённую цифру, вставка из буфера
обмена берёт из неё только цифры. Любые другие символы игнорируются —
привести формат к нужному виду вручную не требуется.
"""

from __future__ import annotations

import customtkinter as ctk

_MAX_DIGITS = 6  # ЧЧ ММ СС
_CTRL_MASK = 0x4
# Клавиши, которым не нужно мешать (навигация, модификаторы, сочетания Ctrl+…).
_PASSTHROUGH_KEYSYMS = {
    "Left",
    "Right",
    "Home",
    "End",
    "Tab",
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Caps_Lock",
    "Escape",
}


def _format(digits: str) -> str:
    """'523' -> '00:05:23'; минуты и секунды зажимаются в 0-59."""
    if not digits:
        return ""
    padded = digits.rjust(_MAX_DIGITS, "0")
    hours, minutes, seconds = padded[:2], padded[2:4], padded[4:6]
    minutes = f"{min(int(minutes), 59):02d}"
    seconds = f"{min(int(seconds), 59):02d}"
    return f"{hours}:{minutes}:{seconds}"


def attach_timecode_mask(entry: ctk.CTkEntry) -> None:
    """Привязывает автоформатирование ЧЧ:ММ:СС к полю ввода."""
    state = {"digits": ""}

    def _resync() -> None:
        """Подхватывает внешние изменения поля (например, entry.delete()
        при смене файла), чтобы буфер цифр не «отставал» от отображаемого
        текста."""
        current_digits = "".join(ch for ch in entry.get() if ch.isdigit())
        if current_digits != state["digits"]:
            state["digits"] = current_digits

    def _redraw() -> None:
        text = _format(state["digits"])
        entry.delete(0, "end")
        if text:
            entry.insert(0, text)

    def _on_key(event) -> str | None:
        if event.state & _CTRL_MASK:
            return None  # не мешаем Ctrl+C/V/A и другим сочетаниям
        keysym = event.keysym
        if keysym in _PASSTHROUGH_KEYSYMS:
            return None
        _resync()
        if keysym in ("BackSpace", "Delete"):
            state["digits"] = state["digits"][:-1]
            _redraw()
            return "break"
        char = event.char
        if char.isdigit():
            state["digits"] = (state["digits"] + char)[-_MAX_DIGITS:]
            _redraw()
        return "break"

    def _on_paste(_event=None) -> str:
        _resync()
        try:
            clipboard = entry.clipboard_get()
        except Exception:  # noqa: BLE001 - буфер обмена пуст/недоступен
            clipboard = ""
        digits = "".join(ch for ch in clipboard if ch.isdigit())
        if digits:
            state["digits"] = (state["digits"] + digits)[-_MAX_DIGITS:]
            _redraw()
        return "break"

    entry.bind("<Key>", _on_key)
    entry.bind("<<Paste>>", _on_paste)
