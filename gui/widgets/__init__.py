"""Переиспользуемые виджеты."""

from __future__ import annotations

from .navigation import NavigationView
from .status_strip import StatusStrip
from .surface import Card, button, muted, severity_color

__all__ = [
    "Card",
    "NavigationView",
    "StatusStrip",
    "button",
    "muted",
    "severity_color",
]
