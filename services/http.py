"""Единая политика сетевых запросов.

Наружу программа ходит в двух местах — за выпусками самой программы и за
сборками FFmpeg, — и правила для обоих одни (повторяют ZapretGUI, §10.3):

* только ``https`` и только к узлам из :data:`ALLOWED_HOSTS`;
* перенаправления обрабатываются вручную, не более трёх, и каждый переход
  проверяется по тому же списку узлов;
* проверка сертификата включена всегда, выключателя нет;
* размер ответа ограничен, загрузка идёт потоком, считает контрольную сумму
  на лету и умеет прерываться.
"""

from __future__ import annotations

import hashlib
import json
import logging
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset(
    {
        # выпуски программы и сборки BtbN
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "raw.githubusercontent.com",
        # сборки gyan.dev
        "www.gyan.dev",
    }
)
MAX_REDIRECTS = 3
MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
READ_TIMEOUT_S = 60.0
DOWNLOAD_CHUNK = 512 * 1024

USER_AGENT = "FFmpegGUI"

#: получено байт, всего байт (0 — размер неизвестен)
ProgressSink = Callable[[int, int], None]


class NetworkError(Exception):
    """Запрос не удался. Текст пригоден для показа пользователю."""


def check_url(url: str) -> str:
    """Возвращает URL, если он проходит политику, иначе бросает исключение."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise NetworkError(f"разрешён только https: {url}")
    if parts.hostname not in ALLOWED_HOSTS:
        raise NetworkError(f"узел не разрешён: {parts.hostname}")
    return url


class _NoRedirect(urllib.request.BaseHandler):
    """Отдаёт перенаправление наружу вместо того, чтобы идти по нему само."""

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: ANN001, D102
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _opener() -> urllib.request.OpenerDirector:
    # Контекст TLS принадлежит обработчику: OpenerDirector.open() не принимает
    # аргумент context. Проверка сертификата — по умолчанию, её не отключаем.
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
    )


def open_url(url: str, headers: dict[str, str] | None = None):
    """Открывает поток ответа, разбирая перенаправления вручную."""
    current = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        request = urllib.request.Request(  # noqa: S310 — схема и узел проверены выше
            current, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            return _opener().open(request, timeout=READ_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                current = check_url(exc.headers.get("Location", ""))
                continue
            raise
    raise NetworkError("слишком много перенаправлений")


def read_bytes(url: str, *, max_bytes: int = MAX_TEXT_BYTES, headers=None) -> bytes:
    with open_url(url, headers) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise NetworkError("ответ слишком большой")
    return data


def read_text(url: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    return read_bytes(url, max_bytes=max_bytes).decode("utf-8", "replace").strip()


def read_json(url: str, *, max_bytes: int = MAX_JSON_BYTES) -> object:
    raw = read_bytes(url, max_bytes=max_bytes, headers={"Accept": "application/vnd.github+json"})
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise NetworkError("не удалось разобрать ответ") from exc


def download(
    url: str,
    destination: Path,
    *,
    progress: ProgressSink | None = None,
    cancelled: threading.Event | None = None,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    expected_sha256: str = "",
) -> Path:
    """Скачивает файл, считая SHA-256 на лету.

    Пишется во временный файл рядом с целевым и переименовывается только после
    успешной проверки — прерванная загрузка не оставит обрубок под нужным именем.
    """
    destination = Path(destination)
    temporary = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        with open_url(url, {"Accept": "application/octet-stream"}) as response:
            total = int(response.headers.get("Content-Length", 0) or 0)
            if total > max_bytes:
                raise NetworkError(f"файл слишком большой: {total} байт")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:
                while True:
                    if cancelled is not None and cancelled.is_set():
                        raise NetworkError("Загрузка отменена.")
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise NetworkError("файл превысил допустимый размер")
                    handle.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(received, total)
    except NetworkError:
        _discard(temporary)
        raise
    except Exception as exc:  # noqa: BLE001 - сеть и файловая система
        _discard(temporary)
        raise NetworkError(readable(exc)) from exc

    if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
        _discard(temporary)
        raise NetworkError("контрольная сумма не совпала — файл отброшен")

    temporary.replace(destination)
    return destination


def readable(exc: Exception) -> str:
    """Причина сбоя человеческими словами."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "Ресурс не найден (404)."
        if exc.code == 403:
            return "Сервер временно ограничил частоту запросов (403)."
        return f"Сервер ответил ошибкой {exc.code}."
    if isinstance(exc, urllib.error.URLError):
        return "Нет соединения с сервером."
    return str(exc) or exc.__class__.__name__


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover
        log.debug("не удалось убрать незавершённую загрузку: %s", exc)
