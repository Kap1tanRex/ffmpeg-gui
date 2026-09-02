"""Категоризация ошибок FFmpeg по выводу stderr.

Раздел ТЗ 27: понятные ошибки вместо сырого текста FFmpeg. Категория
подсказывает пользователю причину и вероятное решение; технический лог
(исходный stderr) остаётся доступным по кнопке «Показать технический лог».
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    NO_ENCODER = "no_encoder"
    NO_DECODER = "no_decoder"
    CORRUPTED_INPUT = "corrupted_input"
    PERMISSION = "permission"
    NO_SPACE = "no_space"
    UNSUPPORTED_CONTAINER = "unsupported_container"
    FILTER_ERROR = "filter_error"
    HWACCEL_ERROR = "hwaccel_error"
    INVALID_ARGUMENT = "invalid_argument"
    OUTPUT_VALIDATION = "output_validation"
    UNKNOWN = "unknown"


@dataclass
class AnalyzedError:
    """Результат анализа: категория + понятная причина + технический лог."""

    category: ErrorCategory
    reason: str
    technical: str = ""
    hint: str = ""

    def report(self) -> str:
        parts = [self.reason]
        if self.hint:
            parts.append(f"Совет: {self.hint}")
        if self.technical:
            parts.append("")
            parts.append("Технический лог:")
            parts.append(self.technical)
        return "\n".join(parts)


# Порядок важен: более специфичные шаблоны проверяются раньше.
_PATTERNS: list[tuple[ErrorCategory, re.Pattern[str], str, str]] = [
    (
        ErrorCategory.NO_ENCODER,
        re.compile(r"Unknown encoder|Encoder not found|is not supported by ffmpeg", re.I),
        "Кодек не поддерживается энкодером в этой сборке FFmpeg.",
        "Выберите другой энкодер на вкладке или обновите FFmpeg.",
    ),
    (
        ErrorCategory.NO_DECODER,
        re.compile(r"Unknown decoder|Decoder \(codec .*\) not found|find_decoder", re.I),
        "Не найден декодер, необходимый для чтения исходного файла.",
        "Установите сборку FFmpeg с поддержкой этого кодека.",
    ),
    (
        ErrorCategory.NO_SPACE,
        re.compile(r"No space left on device", re.I),
        "На диске недостаточно места для записи результата.",
        "Освободите место или выберите другой диск назначения.",
    ),
    (
        ErrorCategory.PERMISSION,
        re.compile(r"Permission denied|Access is denied", re.I),
        "Нет прав на чтение входного файла или запись результата.",
        "Проверьте права доступа к файлам и каталогу назначения.",
    ),
    (
        ErrorCategory.CORRUPTED_INPUT,
        re.compile(
            r"Invalid data found when processing input|moov atom not found|"
            r"Error while decoding stream|corrupt",
            re.I,
        ),
        "Входной файл повреждён или имеет неподдерживаемую структуру.",
        "Попробуйте перезаписать файл заново или открыть его в другом плеере.",
    ),
    (
        ErrorCategory.UNSUPPORTED_CONTAINER,
        re.compile(r"Unknown output format|Invalid argument.*muxer|could not find tag", re.I),
        "Контейнер не принимает выбранную комбинацию кодеков.",
        "Смените контейнер или включите перекодирование потока.",
    ),
    (
        ErrorCategory.FILTER_ERROR,
        re.compile(r"Error initializing filter|No such filter|filtergraph", re.I),
        "Ошибка в графе фильтров.",
        "Проверьте параметры фильтров (масштабирование, субтитры, громкость).",
    ),
    (
        ErrorCategory.HWACCEL_ERROR,
        re.compile(
            r"Cannot load .*nvcuda|nvenc.*failed|No capable devices found|"
            r"Failed to initialise VAAPI|hwaccel.*failed",
            re.I,
        ),
        "Аппаратное ускорение недоступно или драйвер не смог инициализироваться.",
        "Обновите драйвер GPU или переключитесь на программный энкодер.",
    ),
    (
        ErrorCategory.INVALID_ARGUMENT,
        re.compile(r"Invalid argument|Option .* not found|Unrecognized option", re.I),
        "FFmpeg отклонил один из переданных параметров.",
        "Проверьте значения параметров на вкладке «Расширенные параметры».",
    ),
]


class ErrorAnalyzer:
    """Раздел 27: разбор stderr FFmpeg на категории с советом."""

    def analyze(self, stderr_text: str, returncode: int) -> AnalyzedError:
        for category, pattern, reason, hint in _PATTERNS:
            if pattern.search(stderr_text):
                return AnalyzedError(category, reason, stderr_text, hint)
        reason = f"FFmpeg завершился с кодом {returncode}."
        return AnalyzedError(ErrorCategory.UNKNOWN, reason, stderr_text)

    def analyze_validation(self, message: str) -> AnalyzedError:
        """Раздел 54: результат не прошёл проверку FFprobe."""
        return AnalyzedError(
            ErrorCategory.OUTPUT_VALIDATION,
            "Результат не прошёл проверку и не был сохранён.",
            message,
            "Повторите операцию; если ошибка повторяется, сообщите разработчику.",
        )
