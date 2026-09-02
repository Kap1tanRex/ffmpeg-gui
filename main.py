"""Точка входа FFmpeg GUI.

Запуск:
    python main.py
    python -m ffmpeg_gui.main
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PACKAGE_NAME = "ffmpeg_gui"


def _bootstrap_package() -> None:
    """Делает пакет импортируемым при прямом запуске `python main.py`.

    Работает и тогда, когда каталог переименован (например, `FFmpegGUI-main`
    после скачивания архива): пакет регистрируется под своим настоящим именем
    по фактическому пути.
    """
    package_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(package_dir.parent))

    if package_dir.name != PACKAGE_NAME and PACKAGE_NAME not in sys.modules:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            package_dir / "__init__.py",
            submodule_search_locations=[str(package_dir)],
        )
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            sys.modules[PACKAGE_NAME] = module
            spec.loader.exec_module(module)


# Позволяет запускать файл напрямую: python main.py
if __package__ in (None, ""):  # pragma: no cover
    _bootstrap_package()
    __package__ = PACKAGE_NAME

from .app.application import Application  # noqa: E402
from .app.state import APP_VERSION  # noqa: E402

log = logging.getLogger(__name__)

MISSING_GUI_MESSAGE = """
Не найдены библиотеки графического интерфейса.

Установите зависимости:
    pip install -r requirements.txt

Основные пакеты: customtkinter, tkinterdnd2.
На Linux может потребоваться системный пакет python3-tk.
"""


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ffmpeg-gui", description="Графический интерфейс для FFmpeg"
    )
    parser.add_argument("files", nargs="*", help="файлы или папки для импорта при запуске")
    parser.add_argument("--config-dir", help="каталог настроек", default=None)
    parser.add_argument("--log-level", default="INFO", help="уровень логирования")
    parser.add_argument("--version", action="version", version=f"FFmpeg GUI {APP_VERSION}")
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="вывести диагностический отчёт в консоль и выйти",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    arguments = parse_arguments(argv)
    logging.basicConfig(
        level=getattr(logging, arguments.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_dir = Path(arguments.config_dir) if arguments.config_dir else None
    app = Application(config_dir)

    if arguments.diagnostics:
        app.initialize(async_detect=False)
        print(app.diagnostics().to_text())
        app.shutdown()
        return 0

    try:
        from .gui.main_window import MainWindow
    except ImportError as exc:
        log.error("GUI недоступен: %s", exc)
        print(MISSING_GUI_MESSAGE, file=sys.stderr)
        return 2

    window = MainWindow(app)
    if arguments.files:
        window.after(600, lambda: app.import_paths(arguments.files))

    try:
        window.mainloop()
    except KeyboardInterrupt:  # pragma: no cover
        app.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
