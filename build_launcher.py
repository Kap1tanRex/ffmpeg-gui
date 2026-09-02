"""Точка входа для сборки в .exe через PyInstaller.

В отличие от ``main.py``, не полагается на динамический импорт через
``importlib.util.spec_from_file_location`` — PyInstaller не умеет
статически проанализировать такой импорт (он происходит только во время
выполнения), из-за чего пакет ``ffmpeg_gui`` не попал бы в сборку. Здесь
используется обычный, статически видимый ``import``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PARENT = Path(__file__).resolve().parent.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from ffmpeg_gui.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
