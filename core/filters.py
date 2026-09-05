"""Готовые видеофильтры FFmpeg в понятных пользователю терминах.

Модуль отвечает только за строки фильтров: он не знает ни о GUI, ни о Job.
Собранный список кладётся в :attr:`Job.filters` и попадает в ``-vf`` перед
масштабированием — порядок здесь важен, поэтому он задан явно.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Поворот: подпись -> выражение FFmpeg. transpose=1 — по часовой стрелке.
ROTATIONS: dict[str, str] = {
    "Без поворота": "",
    "На 90° вправо": "transpose=1",
    "На 90° влево": "transpose=2",
    "На 180°": "transpose=1,transpose=1",
    "Отразить по горизонтали": "hflip",
    "Отразить по вертикали": "vflip",
}

#: Сила шумоподавления. hqdn3d: пространственные и временные пороги.
DENOISE: dict[str, str] = {
    "Нет": "",
    "Слабое": "hqdn3d=1.5:1.5:6:6",
    "Среднее": "hqdn3d=4:3:6:4.5",
    "Сильное": "hqdn3d=8:6:12:9",
}

#: Положение водяного знака.
POSITIONS: dict[str, str] = {
    "Справа снизу": "br",
    "Слева снизу": "bl",
    "Справа сверху": "tr",
    "Слева сверху": "tl",
    "По центру": "center",
}


@dataclass
class FilterOptions:
    """Что пользователь выбрал в панели фильтров."""

    rotate: str = ""  # выражение из ROTATIONS
    crop_width: int | None = None
    crop_height: int | None = None
    crop_x: int = 0
    crop_y: int = 0
    deinterlace: bool = False
    denoise: str = ""  # выражение из DENOISE

    def crop_filter(self) -> str:
        """``crop=w:h:x:y``; без ширины и высоты кадрировать нечего."""
        if not self.crop_width or not self.crop_height:
            return ""
        x = max(0, int(self.crop_x))
        y = max(0, int(self.crop_y))
        return f"crop={int(self.crop_width)}:{int(self.crop_height)}:{x}:{y}"

    def build(self) -> list[str]:
        """Фильтры в порядке применения.

        Поворот идёт первым: после него меняются местами ширина и высота, и
        кадрировать надо уже повёрнутый кадр. Шумоподавление — последним,
        чтобы работать по меньшей площади и не тратить время зря.
        """
        chain = [
            self.rotate,
            self.crop_filter(),
            "yadif" if self.deinterlace else "",
            self.denoise,
        ]
        return [item for item in chain if item]

    @property
    def active(self) -> bool:
        return bool(self.build())
