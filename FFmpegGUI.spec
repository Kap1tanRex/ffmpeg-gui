# -*- mode: python ; coding: utf-8 -*-
"""Сборка одного самодостаточного .exe (onefile).

Пути вычисляются от самого .spec, а не прописаны жёстко: сборка выполняется
из любого каталога и на любой машине. Готовый файл тоже не привязан к месту —
настройки лежат в %APPDATA%\\FFmpegGUI, а комплектный FFmpeg ищется рядом с
самим .exe (см. services/ffmpeg_service.py).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

PACKAGE_DIR = Path(SPECPATH).resolve()          # ...\\ffmpeg_gui
PROJECT_ROOT = PACKAGE_DIR.parent               # каталог, в котором лежит пакет

datas = [
    # Переводы и палитры тем читаются как обычные файлы рядом с модулями.
    (str(PACKAGE_DIR / "resources" / "translations"), "ffmpeg_gui/resources/translations"),
    (str(PACKAGE_DIR / "gui" / "theming" / "themes"), "ffmpeg_gui/gui/theming/themes"),
]
# CustomTkinter при импорте читает свою тему и шрифты из assets — без них
# приложение падает ещё до создания окна.
datas += collect_data_files("customtkinter")
# tkinterdnd2 несёт бинарники расширения tkdnd; без них перетаскивание файлов
# тихо отключается (приложение продолжает работать через кнопки).
datas += collect_data_files("tkinterdnd2")

a = Analysis(
    [str(PACKAGE_DIR / "build_launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["tkinterdnd2", "PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FFmpegGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
