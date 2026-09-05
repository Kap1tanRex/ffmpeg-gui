"""Одно окно подсказки на всё приложение.

Подсказка не создаётся и не уничтожается на каждое наведение: окно строится
один раз и дальше только показывается, прячется и переезжает.

Так сделано из-за конкретного дефекта: подсказки оставались висеть на экране.
Событие ``<Leave>`` приходит не всегда — курсор может уйти с виджета рывком,
окно может потерять фокус, свернуться или быть перерисованным. Раньше каждое
наведение создавало собственный ``CTkToplevel`` с флагом «поверх всех окон»:
не дождавшись ``<Leave>``, он оставался на экране навсегда — и висел уже
поверх других программ, даже когда само окно приложения переезжало.

Здесь таких окон быть не может: оно ровно одно, любое ``hide()`` его прячет,
а пока подсказка видна, сторож раз в четверть секунды проверяет, что курсор
всё ещё над тем виджетом, который её заказывал.
"""

from __future__ import annotations

import tkinter

import customtkinter as ctk

from ..theming import font, pair, radius

#: Смещение подсказки от точки привязки.
OFFSET_X = 12
OFFSET_Y = 8

#: Как часто сторож проверяет, что курсор всё ещё над виджетом.
WATCH_INTERVAL_MS = 250

#: Порядок и оформление строк подсказки. Ключи совпадают с ролями из
#: :mod:`core.help_registry`: заголовок отвечает «зачем», дальше идут
#: описание, значения и — выделенное цветом — влияние на результат.
_ROLES: tuple[tuple[str, str, bool], ...] = (
    ("title", "fg.primary", True),
    ("body", "fg.secondary", False),
    ("values", "fg.secondary", False),
    ("effect", "state.info", False),
    ("warning", "state.warn", False),
)

_window: ctk.CTkToplevel | None = None
_labels: dict[str, ctk.CTkLabel] = {}
_owner: tkinter.Misc | None = None
_watch_id: str | None = None


def _build(master) -> None:
    """Создаёт окно подсказки. Вызывается один раз при сборке интерфейса."""
    global _window, _labels
    if _window is not None:
        try:
            if _window.winfo_exists():
                return
        except tkinter.TclError:  # pragma: no cover - окно уже уничтожено
            pass
        _reset()
    window = ctk.CTkToplevel(master.winfo_toplevel())
    window.wm_overrideredirect(True)
    window.withdraw()
    try:
        window.attributes("-topmost", True)
    except tkinter.TclError:  # pragma: no cover - зависит от оконного менеджера
        pass
    body = ctk.CTkFrame(window, fg_color=pair("bg.elevated"), corner_radius=radius("sm"))
    body.pack()
    labels: dict[str, ctk.CTkLabel] = {}
    for role, color, bold in _ROLES:
        labels[role] = ctk.CTkLabel(
            body,
            text="",
            justify="left",
            anchor="w",
            text_color=pair(color),
            wraplength=360,
            font=font("small", bold=bold),
        )
    _window, _labels = window, labels


def prepare(master) -> None:
    """Готовит окно подсказки заранее — до первого наведения курсора."""
    try:
        _build(master)
    except tkinter.TclError:  # pragma: no cover - окно уже закрывается
        pass


def show(
    master,
    text: "str | list[tuple[str, str]]",
    x: int,
    y: int,
    *,
    above: bool = False,
) -> None:
    """Показывает подсказку в экранных координатах.

    ``text`` — либо простая строка, либо список ``(роль, текст)``: роли
    перечислены в :data:`_ROLES` и определяют шрифт и цвет строки.
    ``master`` — виджет, над которым стоит курсор: пока подсказка видна, сторож
    следит именно за ним.
    """
    global _owner
    if not text:
        return
    sections = [("body", text)] if isinstance(text, str) else list(text)
    try:
        _build(master)
        if _window is None or not _labels:
            return
        _render(sections)
        # Спрятанное окно ещё не имеет фактического размера — высоту для
        # раскрытия вверх берём из запрошенной менеджером геометрии.
        _window.update_idletasks()
        height = max(_window.winfo_reqheight(), _window.winfo_height())
        top = y - height - OFFSET_Y if above else y + OFFSET_Y
        _window.wm_geometry(f"+{x + OFFSET_X}+{top}")
        _window.deiconify()
        _window.lift()
    except tkinter.TclError:  # pragma: no cover - виджет уже уничтожен
        hide()
        return
    _owner = master
    _start_watch()


def _render(sections: list[tuple[str, str]]) -> None:
    """Раскладывает строки по ролям; лишние прячет, не уничтожая."""
    filled = dict(sections)
    first = True
    for role, _color, _bold in _ROLES:
        label = _labels.get(role)
        if label is None:
            continue
        text = filled.get(role, "")
        if not text:
            label.pack_forget()
            continue
        label.configure(text=text)
        label.pack(anchor="w", padx=10, pady=(9 if first else 5, 0))
        first = False
    # Нижний отступ держит последняя строка: у CTkLabel его иначе нет.
    for role, _color, _bold in reversed(_ROLES):
        label = _labels.get(role)
        if label is not None and label.winfo_manager():
            label.pack_configure(pady=(label.pack_info()["pady"][0], 9))
            break


def hide() -> None:
    """Прячет подсказку. Безопасно вызывать сколько угодно раз."""
    global _owner
    _stop_watch()
    _owner = None
    if _window is None:
        return
    try:
        _window.withdraw()
    except tkinter.TclError:  # pragma: no cover - окно уже уничтожено
        _reset()


# ── сторож ───────────────────────────────────────────────────────────────────
def _start_watch() -> None:
    global _watch_id
    _stop_watch()
    if _window is None:
        return
    try:
        _watch_id = _window.after(WATCH_INTERVAL_MS, _check_pointer)
    except tkinter.TclError:  # pragma: no cover - окно уже уничтожено
        _watch_id = None


def _stop_watch() -> None:
    global _watch_id
    if _watch_id is not None and _window is not None:
        try:
            _window.after_cancel(_watch_id)
        except (tkinter.TclError, ValueError):  # pragma: no cover
            pass
    _watch_id = None


def _check_pointer() -> None:
    """Курсор ушёл, а ``<Leave>`` не пришёл — прячем подсказку сами."""
    global _watch_id
    _watch_id = None
    if _window is None or _owner is None:
        return
    try:
        if not _window.winfo_ismapped():
            return
    except tkinter.TclError:  # pragma: no cover
        _reset()
        return
    if not _pointer_over(_owner):
        hide()
        return
    _start_watch()


def _pointer_over(widget: tkinter.Misc) -> bool:
    try:
        pointer_x, pointer_y = widget.winfo_pointerxy()
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        return (
            left <= pointer_x < left + widget.winfo_width()
            and top <= pointer_y < top + widget.winfo_height()
        )
    except tkinter.TclError:  # виджет уничтожен — подсказке не над чем висеть
        return False


def _reset() -> None:
    global _window, _labels, _owner, _watch_id
    _window = None
    _labels = {}
    _owner = None
    _watch_id = None
