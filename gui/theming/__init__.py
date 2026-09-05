"""Оформление: токены дизайна и сборка темы CustomTkinter."""

from __future__ import annotations

from .theme_manager import (
    APPEARANCE_MODES,
    UI_SCALES,
    apply,
    color,
    font,
    scale_label,
    set_appearance_mode,
    set_ui_scale,
)
from .tokens import pair, radius

__all__ = [
    "APPEARANCE_MODES",
    "UI_SCALES",
    "apply",
    "color",
    "font",
    "pair",
    "radius",
    "scale_label",
    "set_appearance_mode",
    "set_ui_scale",
]
