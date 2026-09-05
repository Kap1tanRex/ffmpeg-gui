"""Простая шина событий Application -> GUI (публикатор/подписчик).

GUI никогда не опрашивает Application напрямую в цикле — он подписывается
на события и обновляется по колбэку. Это разрешает Application публиковать
события из фоновых потоков (обнаружение capabilities, импорт, прогресс
задания), а GUI — переносить обработку в поток Tk через ``after(0, ...)``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from enum import Enum, auto

log = logging.getLogger(__name__)


class Event(Enum):
    FFMPEG_MISSING = auto()
    CAPABILITIES_READY = auto()
    GPU_DETECTED = auto()
    IMPORT_STARTED = auto()
    IMPORT_FINISHED = auto()
    FILES_CHANGED = auto()
    FILE_SELECTED = auto()
    JOB_ADDED = auto()
    JOB_STARTED = auto()
    JOB_PROGRESS = auto()
    JOB_FINISHED = auto()
    JOB_LOG = auto()
    QUEUE_CHANGED = auto()
    QUEUE_IDLE = auto()
    COMMAND_CHANGED = auto()
    SETTINGS_CHANGED = auto()
    UPDATE_CHECKED = auto()
    FFMPEG_UPDATE_CHECKED = auto()
    FFMPEG_UPDATE_PROGRESS = auto()
    FFMPEG_UPDATE_FINISHED = auto()
    WATCH_FILES = auto()


class EventBus:
    """Синхронная шина: publish вызывает подписчиков немедленно."""

    def __init__(self) -> None:
        self._subscribers: dict[Event, list[Callable]] = defaultdict(list)

    def subscribe(self, event: Event, callback: Callable) -> None:
        self._subscribers[event].append(callback)

    def unsubscribe(self, event: Event, callback: Callable) -> None:
        try:
            self._subscribers[event].remove(callback)
        except ValueError:
            pass

    def publish(self, event: Event, *args) -> None:
        for callback in list(self._subscribers.get(event, ())):
            try:
                callback(*args)
            except Exception:  # noqa: BLE001 - один подписчик не должен рушить остальных
                log.exception("Ошибка подписчика на событие %s", event)
