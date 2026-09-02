"""Сравнение версий по правилам SemVer.

Без зависимостей: разбирается ``1.2.3``, ``v1.2.3``, ``1.29.0-dev.7`` и
четырёхчастные версии вида ``1.2.3.4``. Непонятная строка — это данные, а не
ошибка: :meth:`Version.parse` возвращает ``None``, и вызывающий сам решает,
что с этим делать.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_PATTERN = re.compile(
    r"""^\s*v?
    (?P<major>\d+)
    (?:\.(?P<minor>\d+))?
    (?:\.(?P<patch>\d+))?
    (?:\.(?P<build>\d+))?
    (?:[-+](?P<pre>[0-9A-Za-z.\-+]+))?
    \s*$""",
    re.VERBOSE,
)


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0
    build: int = 0
    pre: str = ""
    raw: str = ""

    @classmethod
    def parse(cls, text: str) -> "Version | None":
        if not text:
            return None
        match = _PATTERN.match(text.strip())
        if not match:
            return None
        return cls(
            major=int(match["major"]),
            minor=int(match["minor"] or 0),
            patch=int(match["patch"] or 0),
            build=int(match["build"] or 0),
            pre=(match["pre"] or "").lower(),
            raw=text.strip(),
        )

    @property
    def _key(self) -> tuple[int, int, int, int]:
        return (self.major, self.minor, self.patch, self.build)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key == other._key and self.pre == other.pre

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._key != other._key:
            return self._key < other._key
        # Предрелиз идёт перед своим финальным выпуском: 1.2.0-rc1 < 1.2.0.
        if self.pre and not other.pre:
            return True
        if not self.pre and other.pre:
            return False
        return self.pre < other.pre

    def __hash__(self) -> int:
        return hash((self._key, self.pre))

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.build:
            text += f".{self.build}"
        if self.pre:
            text += f"-{self.pre}"
        return text


#: Ведущий номер версии: «9.0.1-full_build-…», «n8.1.2-50-g1a74…», «v1.2.0».
_NUMERIC_PREFIX = re.compile(r"^[vn]?(\d+(?:\.\d+){0,3})")


def numeric_prefix(text: str) -> str:
    """Только числовая часть версии, без хвоста сборщика.

    FFmpeg подписывает себя как ``9.0.1-full_build-www.gyan.dev``, и если
    сравнивать такую строку целиком, хвост читается как предрелиз — тогда
    установленная сборка вечно оказывается «старее» одноимённой. У ежедневных
    сборок (``N-126390-g9fc8c785e2``) номера нет вовсе, и это честный пустой
    ответ, а не ноль.
    """
    match = _NUMERIC_PREFIX.match((text or "").strip())
    return match.group(1) if match else ""


def is_newer(candidate: str, current: str) -> bool:
    """Строго новее ли ``candidate``.

    Неразбираемая текущая версия означает «неизвестно» — тогда побеждает любая
    разобранная. Неразбираемый кандидат не побеждает никогда: предлагать
    обновление до непонятно чего нельзя.
    """
    new = Version.parse(candidate)
    if new is None:
        return False
    old = Version.parse(current)
    return old is None or new > old
