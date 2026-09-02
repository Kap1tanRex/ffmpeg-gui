"""Сборка темы CustomTkinter из токенов дизайна.

Механика повторяет ZapretGUI: тема — это набор токенов, а таблица стилей
собирается из них по фиксированному шаблону. Здесь роль таблицы стилей играет
словарь темы CustomTkinter (тот же формат, что у встроенного ``blue.json``):
структура задана кодом, из файла темы берутся только значения токенов.

Каждый цвет записывается парой ``(светлый, тёмный)``, поэтому режим оформления
(«Системная» / «Светлая» / «Тёмная») переключается мгновенно, без пересборки
темы и без перезапуска приложения.
"""

from __future__ import annotations

import logging
from pathlib import Path

import customtkinter as ctk

from . import tokens as _tokens
from .tokens import Palette, reload_palette

log = logging.getLogger(__name__)

#: Настройка «Тема» хранит одно из этих значений.
APPEARANCE_MODES = ("System", "Light", "Dark")

_FONT_CACHE: dict[tuple, ctk.CTkFont] = {}


def build_theme(palette: Palette) -> dict:
    """Словарь темы CustomTkinter, собранный из токенов палитры."""
    color = palette.pair
    sm = palette.size("radius.sm")
    md = palette.size("radius.md")

    # Поля ввода и выпадающие списки заливаются одним «тихим» тоном: в светлой
    # теме поверхность карточки белая, и элемент без заливки на ней исчезает.
    control = color("bg.hover")
    control_hover = color("bg.selected")

    return {
        "CTk": {"fg_color": color("bg.window")},
        "CTkToplevel": {"fg_color": color("bg.window")},
        "CTkFrame": {
            "corner_radius": md,
            "border_width": 0,
            "fg_color": color("bg.surface"),
            "top_fg_color": color("bg.elevated"),
            "border_color": color("border"),
        },
        "CTkButton": {
            # Обычная кнопка — вторичная: поверхность и рамка, без акцента.
            # Акцентную собирает помощник widgets.surface.button(primary=True).
            "corner_radius": sm,
            "border_width": 1,
            "fg_color": color("bg.elevated"),
            "hover_color": color("bg.hover"),
            "border_color": color("border.strong"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkLabel": {
            "corner_radius": 0,
            "border_width": 0,
            "fg_color": "transparent",
            "border_color": color("border"),
            "text_color": color("fg.primary"),
        },
        "CTkEntry": {
            "corner_radius": sm,
            "border_width": 1,
            "fg_color": control,
            "border_color": color("border.strong"),
            "text_color": color("fg.primary"),
            "placeholder_text_color": color("fg.disabled"),
        },
        "CTkCheckBox": {
            "corner_radius": 4,
            "border_width": 2,
            "fg_color": color("accent"),
            "border_color": color("border.strong"),
            "hover_color": color("accent.hover"),
            "checkmark_color": color("accent.fg"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkSwitch": {
            "corner_radius": 1000,
            "border_width": 3,
            "button_length": 0,
            "fg_color": color("border.strong"),
            "progress_color": color("accent"),
            "button_color": color("fg.secondary"),
            "button_hover_color": color("fg.primary"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkRadioButton": {
            "corner_radius": 1000,
            "border_width_checked": 6,
            "border_width_unchecked": 2,
            "fg_color": color("accent"),
            "border_color": color("border.strong"),
            "hover_color": color("accent.hover"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkProgressBar": {
            "corner_radius": 1000,
            "border_width": 0,
            "fg_color": control,
            "progress_color": color("accent"),
            "border_color": color("border"),
        },
        "CTkSlider": {
            "corner_radius": 1000,
            "button_corner_radius": 1000,
            "border_width": 6,
            "button_length": 0,
            "fg_color": color("border.strong"),
            "progress_color": color("accent"),
            "button_color": color("accent"),
            "button_hover_color": color("accent.hover"),
        },
        "CTkOptionMenu": {
            "corner_radius": sm,
            "fg_color": control,
            "button_color": control_hover,
            "button_hover_color": color("border.strong"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkComboBox": {
            "corner_radius": sm,
            "border_width": 1,
            "fg_color": control,
            "border_color": color("border.strong"),
            "button_color": color("border.strong"),
            "button_hover_color": color("fg.disabled"),
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkScrollbar": {
            "corner_radius": 1000,
            "border_spacing": 4,
            "fg_color": "transparent",
            "button_color": color("border.strong"),
            "button_hover_color": color("fg.disabled"),
        },
        "CTkSegmentedButton": {
            # Выбранный сегмент подсвечивается так же, как активный пункт
            # бокового меню: заливка выбранного, обычный цвет текста.
            "corner_radius": sm,
            "border_width": 2,
            "fg_color": control,
            "selected_color": color("bg.selected"),
            "selected_hover_color": color("bg.selected"),
            "unselected_color": control,
            "unselected_hover_color": control_hover,
            "text_color": color("fg.primary"),
            "text_color_disabled": color("fg.disabled"),
        },
        "CTkTextbox": {
            "corner_radius": sm,
            "border_width": 1,
            "fg_color": color("bg.elevated"),
            "border_color": color("border"),
            "text_color": color("fg.primary"),
            "scrollbar_button_color": color("border.strong"),
            "scrollbar_button_hover_color": color("fg.disabled"),
        },
        "CTkScrollableFrame": {"label_fg_color": color("bg.surface")},
        "DropdownMenu": {
            "fg_color": color("bg.elevated"),
            "hover_color": color("bg.selected"),
            "text_color": color("fg.primary"),
        },
        "CTkFont": {
            "family": palette.family(),
            "size": palette.size("font.size"),
            "weight": "normal",
        },
    }


def apply(mode: str = "System", *, user_dir: Path | None = None, accent: str | None = None) -> None:
    """Применяет тему и режим оформления.

    Вызывается до создания корневого окна: виджеты CustomTkinter читают
    значения темы в момент создания.
    """
    palette = reload_palette(user_dir)
    if accent:
        palette.set_accent(accent)
    ctk.ThemeManager.theme = build_theme(palette)
    ctk.ThemeManager._currently_loaded_theme = "ffmpeg-gui"
    set_appearance_mode(mode)
    _FONT_CACHE.clear()


def set_appearance_mode(mode: str) -> None:
    """Системная / Светлая / Тёмная — переключается без перезапуска."""
    ctk.set_appearance_mode(mode if mode in APPEARANCE_MODES else "System")


def color(token: str) -> str:
    """Токен в один конкретный цвет — для виджетов голого tkinter.

    CustomTkinter сам выбирает половину пары ``(светлый, тёмный)``, а
    ``tkinter.Frame`` так не умеет и требует готовую строку.
    """
    light, dark = _tokens.PALETTE.pair(token)
    return dark if ctk.get_appearance_mode() == "Dark" else light


def font(role: str = "body", *, bold: bool = False) -> ctk.CTkFont:
    """Шрифт по роли: ``body``, ``small``, ``large``, ``hero``, ``mono``.

    Требует уже созданного корневого окна, поэтому вызывается при построении
    виджетов, а не на уровне модуля.
    """
    key = (role, bold)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    palette = _tokens.PALETTE
    sizes = {
        "body": palette.size("font.size"),
        "small": palette.size("font.size.small"),
        "large": palette.size("font.size.large"),
        "hero": palette.size("font.size.hero"),
        "mono": palette.size("font.size.small"),
    }
    family = palette.family("font.family.mono" if role == "mono" else "font.family")
    created = ctk.CTkFont(
        family=family,
        size=sizes.get(role, palette.size("font.size")),
        weight="bold" if bold else "normal",
    )
    _FONT_CACHE[key] = created
    return created
