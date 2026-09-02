"""Обновление самой сборки FFmpeg.

Два источника на выбор (настройка «Источник сборок»):

* **gyan.dev** — сборки с сайта, который рекомендует ffmpeg.org и которым
  пользуется пакет ``Gyan.FFmpeg``. Отдаёт номерную версию (``9.0.1``) и
  контрольную сумму, но упакован в ``.7z``: нужен внешний распаковщик.
* **BtbN/FFmpeg-Builds** — выпуски на GitHub, обычные ``.zip``, которые
  распаковывает стандартная библиотека. Кроме сборок с веток выпусков там
  есть ежедневные из ``master`` — у них нет номера версии, только дата.

Скачанное кладётся в корень приложения (``<корень>/ffmpeg/bin``) — туда, куда
и так смотрит :mod:`services.ffmpeg_service`, так что подхватывается само.
Ничего не делается втихую: проверка и установка запускаются кнопками.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from ..core.version import Version, is_newer, numeric_prefix
from .ffmpeg_service import creation_flags
from .http import ProgressSink, NetworkError, download, read_json, read_text

log = logging.getLogger(__name__)

#: Идентификаторы источников — они же значения настройки.
SOURCE_GYAN = "gyan"
SOURCE_BTBN = "btbn"
SOURCES = (SOURCE_GYAN, SOURCE_BTBN)

SOURCE_LABELS = {
    SOURCE_GYAN: "gyan.dev — полная сборка",
    SOURCE_BTBN: "BtbN/FFmpeg-Builds — win64 GPL",
}

GYAN_BASE = "https://www.gyan.dev/ffmpeg/builds/"
BTBN_RELEASES = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=5"

#: Из сборки берутся только те программы, которые запускает приложение.
#: ffplay весит ещё около 220 МБ и здесь не нужен.
WANTED = ("ffmpeg.exe", "ffprobe.exe")

#: Сколько места нужно на всё про всё: архив, распакованное и прежняя папка.
REQUIRED_FREE_BYTES = 2 * 1024 * 1024 * 1024

#: «ffmpeg-<версия>-win64-gpl[-<ветка>].zip». Версия сама бывает с дефисами
#: («master-latest», «n8.1.2-50-g1a748fe2cd»), а ``-shared`` в конце — это
#: сборка с отдельными DLL, она нам не подходит.
_BTBN_ASSET = re.compile(r"^ffmpeg-(?P<version>.+)-win64-gpl(?:-[\d.]+)?\.zip$")


class FFmpegUpdateError(Exception):
    """Обновление сборки не удалось. Текст пригоден для показа пользователю."""


@dataclass(frozen=True)
class FFmpegBuild:
    """Сборка, доступная к установке."""

    source: str
    version: str
    url: str
    size: int = 0
    sha256: str = ""
    published_at: datetime | None = None
    label: str = ""

    @property
    def is_archive_7z(self) -> bool:
        return self.url.lower().endswith(".7z")

    @property
    def date_text(self) -> str:
        return self.published_at.strftime("%Y-%m-%d") if self.published_at else ""


@dataclass
class FFmpegUpdateState:
    """Что известно о сборках прямо сейчас."""

    checked_at: datetime | None = None
    installed: str = ""
    build: FFmpegBuild | None = None
    newer: bool = False
    error: str = ""


def app_ffmpeg_dir(root: Path) -> Path:
    """Куда кладётся сборка: ``<корень>/ffmpeg/bin``."""
    return Path(root) / "ffmpeg" / "bin"


class FFmpegUpdater:
    """Узнаёт, что вышло, и ставит выбранную сборку в корень приложения."""

    def __init__(self, source: str = SOURCE_GYAN) -> None:
        self.source = source if source in SOURCES else SOURCE_GYAN
        self._cancelled = threading.Event()

    # -- проверка ------------------------------------------------------------
    def latest(self) -> FFmpegBuild:
        """Самая свежая сборка выбранного источника."""
        if self.source == SOURCE_BTBN:
            return self._latest_btbn()
        return self._latest_gyan()

    def check(self, installed_version: str) -> FFmpegUpdateState:
        """Сравнивает установленную сборку с доступной.

        Сбой сети — это состояние, а не исключение: интерфейсу нужна причина.
        """
        try:
            build = self.latest()
        except (FFmpegUpdateError, NetworkError) as exc:
            return FFmpegUpdateState(
                checked_at=datetime.now(), installed=installed_version, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - сетевые сбои приходят разными типами
            log.warning("проверка сборки FFmpeg не удалась: %s", exc)
            return FFmpegUpdateState(
                checked_at=datetime.now(), installed=installed_version, error=str(exc)
            )

        # Версия FFmpeg приходит с хвостом сборщика («9.0.1-full_build-www.gyan.dev»),
        # а у ежедневных сборок номера нет вовсе — сравниваем только числа.
        current = numeric_prefix(installed_version)
        candidate = numeric_prefix(build.version)
        newer = bool(candidate) and is_newer(candidate, current) if current else bool(candidate)
        return FFmpegUpdateState(
            checked_at=datetime.now(), installed=installed_version, build=build, newer=newer
        )

    def _latest_gyan(self) -> FFmpegBuild:
        version = read_text(GYAN_BASE + "release-version")
        if not version:
            raise FFmpegUpdateError("gyan.dev не сообщил версию сборки.")
        sha = ""
        try:
            sha = read_text(GYAN_BASE + "ffmpeg-release-full.7z.sha256").split()[0]
        except (NetworkError, IndexError):
            log.info("gyan.dev не отдал контрольную сумму — проверка суммы пропущена")
        return FFmpegBuild(
            source=SOURCE_GYAN,
            version=version,
            url=GYAN_BASE + "ffmpeg-release-full.7z",
            sha256=sha,
            label=f"gyan.dev · полная сборка {version}",
        )

    def _latest_btbn(self) -> FFmpegBuild:
        payload = read_json(BTBN_RELEASES)
        if not isinstance(payload, list) or not payload:
            raise FFmpegUpdateError("GitHub не вернул выпуски BtbN/FFmpeg-Builds.")
        for entry in payload:
            if not isinstance(entry, dict) or entry.get("draft"):
                continue
            asset, version = _pick_btbn_asset(entry.get("assets") or [])
            if asset is None:
                continue
            return FFmpegBuild(
                source=SOURCE_BTBN,
                version=version,
                url=str(asset.get("browser_download_url", "")),
                size=int(asset.get("size", 0) or 0),
                published_at=_parse_time(entry.get("published_at")),
                label=f"BtbN · {asset.get('name', '')}",
            )
        raise FFmpegUpdateError("Среди выпусков BtbN не нашлось сборки win64 GPL.")

    # -- установка -----------------------------------------------------------
    def install(
        self, build: FFmpegBuild, root: Path, progress: ProgressSink | None = None
    ) -> Path:
        """Скачивает сборку и раскладывает её в ``<корень>/ffmpeg/bin``.

        Порядок такой, чтобы неудача на любом шаге не оставила приложение без
        рабочего FFmpeg: сначала всё складывается рядом в ``bin.new``, новый
        ffmpeg проверяется запуском, и только потом папки меняются местами.
        """
        self._cancelled.clear()
        target = app_ffmpeg_dir(root)
        work = target.parent / ".update"
        staging = target.parent / "bin.new"
        previous = target.parent / "bin.old"

        _require_space(target.parent)
        _reset(work)
        _reset(staging)
        _reset(previous)

        try:
            archive = work / _archive_name(build)
            download(
                build.url,
                archive,
                progress=progress,
                cancelled=self._cancelled,
                expected_sha256=build.sha256,
            )
            extracted = _extract(archive, staging)
            if not extracted:
                raise FFmpegUpdateError("В архиве не нашлось ffmpeg.exe и ffprobe.exe.")
            version = _probe_version(staging / "ffmpeg.exe")

            if target.exists():
                target.rename(previous)
            staging.rename(target)
            log.info("установлена сборка FFmpeg %s из %s", version, build.source)
        except NetworkError as exc:
            raise FFmpegUpdateError(str(exc)) from exc
        finally:
            _reset(work)
            _reset(staging)

        _reset(previous)
        return target / "ffmpeg.exe"

    def cancel(self) -> None:
        """Просит идущую загрузку остановиться. Вызывать безопасно всегда."""
        self._cancelled.set()


# ── распаковка ───────────────────────────────────────────────────────────────
def _extract(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        return _extract_zip(archive, destination)
    return _extract_7z(archive, destination)


def _extract_zip(archive: Path, destination: Path) -> list[Path]:
    """Из архива берутся только нужные .exe, и только по имени файла.

    Путь из архива не используется вовсе — так запись не может уйти за пределы
    каталога назначения, каким бы ни было содержимое архива.
    """
    written: list[Path] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename).name
            if name.lower() not in WANTED or info.is_dir():
                continue
            target = destination / name
            with zf.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            written.append(target)
    return written


def _extract_7z(archive: Path, destination: Path) -> list[Path]:
    """7-Zip или системный tar: libarchive в ``tar.exe`` умеет читать 7z."""
    tool = _seven_zip_tool()
    if tool is None:
        raise FFmpegUpdateError(
            "Архив .7z нечем распаковать: не найдены 7-Zip и системный tar. "
            "Установите 7-Zip или выберите источник BtbN — он отдаёт zip."
        )
    name, command = tool
    try:
        result = subprocess.run(  # noqa: S603 - список аргументов, shell=False
            command(archive, destination),
            capture_output=True,
            timeout=600,
            creationflags=creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegUpdateError(f"{name} не смог распаковать архив: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise FFmpegUpdateError(
            f"{name} завершился с ошибкой: {detail[-1] if detail else result.returncode}"
        )

    # Распаковщик мог сохранить вложенные каталоги — собираем нужное наверх.
    written: list[Path] = []
    for path in sorted(destination.rglob("*.exe")):
        if path.name.lower() not in WANTED:
            path.unlink(missing_ok=True)
            continue
        final = destination / path.name
        if path != final:
            path.replace(final)
        written.append(final)
    for leftover in sorted(destination.glob("*/"), reverse=True):
        shutil.rmtree(leftover, ignore_errors=True)
    return written


def _seven_zip_tool():
    """Чем распаковывать 7z: (название, построитель команды) либо ``None``."""
    seven = shutil.which("7z") or shutil.which("7za")
    if seven:
        return "7-Zip", lambda archive, dest: [
            seven, "x", "-y", f"-o{dest}", str(archive), *WANTED, "-r",
        ]
    tar = _bsdtar()
    if tar:
        return "tar", lambda archive, dest: [
            tar, "-xf", str(archive), "-C", str(dest), "--strip-components=2", *(
                f"*/bin/{name}" for name in WANTED
            ),
        ]
    return None


def _bsdtar() -> str | None:
    """Путь к ``tar`` на libarchive — только он читает 7z.

    В PATH может оказаться GNU tar (например, из состава Git): формат 7z он не
    понимает вовсе, а путь с буквой диска принимает за адрес удалённой машины.
    Поэтому системный ``tar.exe`` пробуется первым и в любом случае каждый
    кандидат проверяется по его же ``--version``.
    """
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [Path(system_root) / "System32" / "tar.exe", shutil.which("tar")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            result = subprocess.run(  # noqa: S603 - путь из системы, аргумент фиксирован
                [str(path), "--version"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=15,
                creationflags=creation_flags(),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if "libarchive" in (result.stdout or "").lower():
            return str(path)
    return None


# ── вспомогательное ──────────────────────────────────────────────────────────
def _pick_btbn_asset(assets: list) -> tuple[dict | None, str]:
    """Статическая сборка win64 GPL — ближайший аналог полной сборки gyan.dev.

    Предпочитается сборка с ветки выпуска (``n8.1``): у неё есть номер, и её
    можно сравнить с установленной. Ежедневная из ``master`` берётся, только
    если веток выпусков в этом релизе нет — сравнивать её не с чем.
    """
    candidates: list[tuple[dict, str]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        match = _BTBN_ASSET.match(str(asset.get("name", "")))
        if match:
            candidates.append((asset, match.group("version").lstrip("n")))
    if not candidates:
        return None, ""

    numbered = [(a, v) for a, v in candidates if numeric_prefix(v)]
    if numbered:
        return max(numbered, key=lambda item: Version.parse(numeric_prefix(item[1])) or Version(0))
    return candidates[0]


def _archive_name(build: FFmpegBuild) -> str:
    suffix = ".7z" if build.is_archive_7z else ".zip"
    version = re.sub(r"[^A-Za-z0-9._-]", "_", build.version or "latest")
    return f"ffmpeg-{version}{suffix}"


def _probe_version(binary: Path) -> str:
    """Запускает скачанный ffmpeg: не запустился — не устанавливаем."""
    try:
        result = subprocess.run(  # noqa: S603 - путь наш, аргументы фиксированы
            [str(binary), "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FFmpegUpdateError(f"Скачанный ffmpeg.exe не запускается: {exc}") from exc
    if result.returncode != 0:
        raise FFmpegUpdateError("Скачанный ffmpeg.exe не запускается.")
    first = (result.stdout or "").splitlines()
    return first[0] if first else ""


def _require_space(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < REQUIRED_FREE_BYTES:
        raise FFmpegUpdateError(
            f"Недостаточно места: нужно около {REQUIRED_FREE_BYTES // 1024**3} ГБ, "
            f"свободно {free / 1024**3:.1f} ГБ."
        )


def _reset(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
