"""Подготовка окружения для pytest.

Регистрирует пакет под именем ``ffmpeg_gui`` независимо от того, как назван
каталог на диске (например, ``FFmpegGUI-main`` после распаковки архива) и из
какого каталога запущен pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_NAME = "ffmpeg_gui"
PACKAGE_DIR = Path(__file__).resolve().parent

if str(PACKAGE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(PACKAGE_DIR.parent))

if PACKAGE_NAME not in sys.modules and PACKAGE_DIR.name != PACKAGE_NAME:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PACKAGE_DIR / "__init__.py",
        submodule_search_locations=[str(PACKAGE_DIR)],
    )
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = module
        spec.loader.exec_module(module)
