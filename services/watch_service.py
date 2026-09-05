"""Папка наблюдения: новые файлы сами уходят в очередь.

Опрос, а не системные уведомления: опрос раз в несколько секунд стоит
дешевле, чем зависимость от сторонней библиотеки, и одинаково работает на
всех платформах.

Главная тонкость — момент, когда файл можно брать в работу. Только что
появившийся файл может ещё дописываться: копироваться с флешки, скачиваться,
выгружаться из монтажной программы. Поэтому файл считается готовым лишь
после того, как его размер не изменился между двумя проверками подряд.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

log = logging.getLogger(__name__)

#: Как часто заглядывать в папку.
DEFAULT_INTERVAL = 5.0


class WatchFolder:
    """Следит за одной папкой и сообщает о готовых файлах."""

    def __init__(
        self,
        on_ready: Callable[[list[Path]], None],
        extensions: Iterable[str] = (),
        interval: float = DEFAULT_INTERVAL,
    ) -> None:
        self.on_ready = on_ready
        self.extensions = {e.lower().lstrip(".") for e in extensions}
        self.interval = max(1.0, interval)
        self.directory: Path | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Файл -> размер на прошлой проверке. Пока размер растёт, файл ещё пишется.
        self._sizes: dict[Path, int] = {}
        #: Уже отданные в очередь — повторно не берём.
        self._done: set[Path] = set()

    # -- управление -----------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, directory: Path | str) -> None:
        """Начинает следить за папкой. Файлы, лежащие там сейчас, не трогает.

        Иначе включение наблюдения означало бы разом поставить в очередь всё
        накопленное содержимое папки — почти никогда не то, чего хотят.
        """
        self.stop()
        self.directory = Path(directory)
        self._sizes.clear()
        self._done = set(self._candidates())
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info("Наблюдение за папкой начато: %s", self.directory)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self.interval + 1.0)

    # -- работа ---------------------------------------------------------------
    def _candidates(self) -> list[Path]:
        if self.directory is None or not self.directory.is_dir():
            return []
        found: list[Path] = []
        try:
            entries = list(self.directory.iterdir())
        except OSError as exc:
            log.warning("Папка наблюдения недоступна: %s", exc)
            return []
        for path in entries:
            if not path.is_file():
                continue
            if self.extensions and path.suffix.lower().lstrip(".") not in self.extensions:
                continue
            found.append(path)
        return found

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            ready = self._collect_ready()
            if ready:
                try:
                    self.on_ready(ready)
                except Exception:  # noqa: BLE001 - чужой колбэк не должен убивать поток
                    log.exception("Ошибка обработки новых файлов")

    def _collect_ready(self) -> list[Path]:
        """Файлы, чей размер перестал меняться с прошлой проверки."""
        ready: list[Path] = []
        seen: set[Path] = set()
        for path in self._candidates():
            seen.add(path)
            if path in self._done:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            previous = self._sizes.get(path)
            self._sizes[path] = size
            # Нулевой размер — файл только создан, ждём следующей проверки.
            if previous is not None and previous == size and size > 0:
                self._done.add(path)
                self._sizes.pop(path, None)
                ready.append(path)
        # Забываем исчезнувшие файлы, иначе множества растут без конца.
        self._sizes = {p: s for p, s in self._sizes.items() if p in seen}
        self._done &= seen
        return ready
