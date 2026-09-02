"""Проверка обновлений самой программы через GitHub Releases.

Сетевая политика — общая для всей программы, см. :mod:`services.http`.
Здесь к ней добавлена одна проверка: имя репозитория разбирается по образцу
``владелец/имя``, поэтому в URL не попадёт ни ``..``, ни параметры запроса.

Ничего не устанавливается автоматически: служба только узнаёт, что вышло, и
по отдельной команде скачивает файл выпуска туда, куда указал пользователь.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.version import Version, is_newer
from .http import (
    MAX_ARCHIVE_BYTES,
    MAX_JSON_BYTES,
    NetworkError,
    ProgressSink,
    check_url,
    download,
    read_json,
    readable,
)

log = logging.getLogger(__name__)

#: Репозиторий, у которого спрашиваются выпуски. Задаётся в настройках.
#: Пусто по умолчанию: выдуманный адрес означал бы бесполезный запрос в сеть
#: при каждом запуске.
DEFAULT_REPOSITORY = ""

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}/[A-Za-z0-9._-]{1,64}$")

#: Ответ GitHub держится в памяти час: у анонимных запросов есть лимит частоты.
CACHE_TTL_S = 3600.0

#: Ошибка обновления — это ошибка сети: отдельного класса она не заслуживает,
#: а имя оставлено, потому что по нему её ловит интерфейс.
UpdateError = NetworkError


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class ReleaseInfo:
    """Один выпуск: чем он называется, когда вышел и что в нём."""

    version: Version
    tag: str
    published_at: datetime | None = None
    notes: str = ""
    prerelease: bool = False
    page_url: str = ""
    asset: ReleaseAsset | None = None

    @property
    def date_text(self) -> str:
        return self.published_at.strftime("%Y-%m-%d") if self.published_at else ""


@dataclass
class UpdateState:
    """Что известно об обновлениях прямо сейчас."""

    checked_at: datetime | None = None
    latest: ReleaseInfo | None = None
    #: Все выпуски новее установленного — их и показывает окно обновления:
    #: пропустив пару версий, пользователь должен увидеть обе.
    newer: tuple[ReleaseInfo, ...] = field(default_factory=tuple)
    releases: tuple[ReleaseInfo, ...] = field(default_factory=tuple)
    error: str = ""

    @property
    def available(self) -> bool:
        return self.latest is not None


def normalize_repository(repository: str) -> str:
    """Проверяет «владелец/имя». Пустая строка означает «не настроено»."""
    text = (repository or "").strip().strip("/")
    if not text:
        return ""
    if not _REPOSITORY_RE.match(text):
        raise UpdateError(f"некорректное имя репозитория: {repository!r}")
    return text


class UpdateService:
    """Спрашивает у GitHub список выпусков и умеет скачать файл выпуска."""

    def __init__(self, repository: str = DEFAULT_REPOSITORY) -> None:
        self.repository = repository
        self._cache: dict[str, tuple[float, object]] = {}
        self._cancelled = threading.Event()

    # -- проверка ------------------------------------------------------------
    def releases(self, *, limit: int = 10, prerelease: bool = False) -> tuple[ReleaseInfo, ...]:
        """Последние выпуски, новые сверху. Бросает :class:`UpdateError`."""
        repository = normalize_repository(self.repository)
        if not repository:
            raise UpdateError("Репозиторий обновлений не задан в настройках.")

        url = f"https://api.github.com/repos/{repository}/releases?per_page=30"
        payload = self._json(url)
        if not isinstance(payload, list):
            raise UpdateError("Неожиданный ответ GitHub.")

        found: list[ReleaseInfo] = []
        for entry in payload:
            if not isinstance(entry, dict) or entry.get("draft"):
                continue
            if entry.get("prerelease") and not prerelease:
                continue
            info = _to_info(entry)
            if info is not None:
                found.append(info)
            if len(found) >= limit:
                break
        return tuple(found)

    def check(self, current_version: str, *, prerelease: bool = False) -> UpdateState:
        """Есть ли выпуск новее установленного.

        Ошибка сети — это состояние, а не исключение: интерфейсу нужно показать
        причину, а не упасть.
        """
        try:
            releases = self.releases(prerelease=prerelease)
        except UpdateError as exc:
            return UpdateState(checked_at=datetime.now(), error=str(exc))
        except Exception as exc:  # noqa: BLE001 - сетевые сбои приходят разными типами
            log.warning("проверка обновлений не удалась: %s", exc)
            return UpdateState(checked_at=datetime.now(), error=readable(exc))

        newer = tuple(r for r in releases if is_newer(r.tag, current_version))
        return UpdateState(
            checked_at=datetime.now(),
            latest=newer[0] if newer else None,
            newer=newer,
            releases=releases,
            error="",
        )

    # -- загрузка ------------------------------------------------------------
    def download(
        self, asset: ReleaseAsset, destination: Path, progress: ProgressSink | None = None
    ) -> Path:
        """Скачивает файл выпуска в ``destination``. Бросает :class:`UpdateError`.

        Пишется во временный файл рядом с целевым и переименовывается только
        после успеха — прерванная загрузка не оставит обрубок под нужным именем.
        """
        self._cancelled.clear()
        return download(
            asset.url,
            Path(destination),
            progress=progress,
            cancelled=self._cancelled,
            max_bytes=MAX_ARCHIVE_BYTES,
        )

    def cancel(self) -> None:
        """Просит идущую загрузку остановиться. Вызывать безопасно всегда."""
        self._cancelled.set()

    # -- внутреннее ----------------------------------------------------------
    def _json(self, url: str) -> object:
        import time

        cached = self._cache.get(url)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
            return cached[1]
        payload = read_json(url, max_bytes=MAX_JSON_BYTES)
        self._cache[url] = (time.monotonic(), payload)
        return payload


def _to_info(entry: dict) -> ReleaseInfo | None:
    tag = str(entry.get("tag_name") or "")
    version = Version.parse(tag)
    if version is None:
        return None
    return ReleaseInfo(
        version=version,
        tag=tag,
        published_at=_parse_time(entry.get("published_at")),
        notes=str(entry.get("body") or "")[:8000],
        prerelease=bool(entry.get("prerelease")),
        page_url=str(entry.get("html_url") or ""),
        asset=_pick_asset(entry.get("assets") or []),
    )


def _pick_asset(assets: list) -> ReleaseAsset | None:
    """Из вложений выпуска берётся исполняемый файл, иначе архив."""
    candidates = [a for a in assets if isinstance(a, dict) and a.get("browser_download_url")]
    for suffixes in ((".exe",), (".zip", ".7z"), (".tar.gz", ".tgz")):
        matching = [a for a in candidates if str(a.get("name", "")).lower().endswith(suffixes)]
        if matching:
            best = max(matching, key=lambda a: int(a.get("size", 0) or 0))
            return ReleaseAsset(
                name=str(best.get("name", "")),
                url=str(best.get("browser_download_url", "")),
                size=int(best.get("size", 0) or 0),
            )
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
