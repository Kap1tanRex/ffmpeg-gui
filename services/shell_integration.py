"""Пункт «Открыть в FFmpeg GUI» в контекстном меню проводника (Windows).

Запись идёт только в ветку текущего пользователя (``HKEY_CURRENT_USER``):
права администратора не нужны, на других пользователей это не влияет, и
удаляется всё одной кнопкой. Ничего не делается само: и добавление, и
удаление происходят только по прямому нажатию пользователя в настройках.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: Имя ключа в реестре — по нему пункт и находится при удалении.
KEY_NAME = "FFmpegGUI"
MENU_TITLE = "Открыть в FFmpeg GUI"

#: Категории файлов, к которым добавляется пункт. Windows сама относит к ним
#: конкретные расширения, поэтому список расширений вести не нужно.
CATEGORIES = ("video", "audio")

_BASE = r"Software\Classes\SystemFileAssociations\{category}\shell\{key}"


class ShellIntegrationError(RuntimeError):
    """Реестр недоступен или запись не удалась."""


def available() -> bool:
    return sys.platform == "win32"


def _winreg():
    if not available():
        raise ShellIntegrationError("Интеграция с проводником есть только в Windows.")
    import winreg

    return winreg


def executable_command() -> str:
    """Команда запуска с подстановкой пути к файлу.

    У собранного .exe это он сам; при запуске из исходников — интерпретатор
    Python и main.py, иначе пункт меню открывал бы пустое окно консоли.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" "%1"'
    entry = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{Path(sys.executable).resolve()}" "{entry}" "%1"'


def is_registered() -> bool:
    if not available():
        return False
    winreg = _winreg()
    path = _BASE.format(category=CATEGORIES[0], key=KEY_NAME)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path):
            return True
    except OSError:
        return False


def register() -> None:
    """Добавляет пункт меню для видео- и аудиофайлов."""
    winreg = _winreg()
    command = executable_command()
    icon = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
    for category in CATEGORIES:
        path = _BASE.format(category=category, key=KEY_NAME)
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, MENU_TITLE)
                if icon is not None:
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(icon))
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r"\command") as key:
                winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
        except OSError as exc:
            raise ShellIntegrationError(f"Не удалось записать в реестр: {exc}") from exc


def unregister() -> None:
    """Убирает пункт меню. Отсутствие ключа ошибкой не считается."""
    winreg = _winreg()
    for category in CATEGORIES:
        path = _BASE.format(category=category, key=KEY_NAME)
        for suffix in (r"\command", ""):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path + suffix)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ShellIntegrationError(f"Не удалось удалить ключ: {exc}") from exc
