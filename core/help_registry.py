"""Тексты контекстных подсказок ⓘ рядом с параметрами (раздел 32).

Каждая запись содержит краткое пояснение (для тултипа), подробное описание
(для окна справки) и необязательное предупреждение. GUI запрашивает запись
по ключу параметра; отсутствующий ключ не считается ошибкой — виджет
подсказки просто не показывается.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HelpEntry:
    brief: str
    detailed: str = ""
    warning: str = ""

    def text(self) -> str:
        parts = [self.brief]
        if self.detailed:
            parts.append(self.detailed)
        if self.warning:
            parts.append(f"⚠ {self.warning}")
        return "\n\n".join(parts)


class HelpRegistry:
    """Реестр подсказок; заполняется базовыми записями по умолчанию."""

    def __init__(self) -> None:
        self._entries: dict[str, HelpEntry] = {}
        self._register_defaults()

    def register(self, key: str, entry: HelpEntry) -> None:
        self._entries[key] = entry

    def get(self, key: str) -> HelpEntry | None:
        return self._entries.get(key)

    def text_for(self, key: str, default: str = "") -> str:
        entry = self.get(key)
        return entry.text() if entry else default

    def encoder_hint(self, encoder: str) -> str:
        """Раздел 32: контекстная подсказка для конкретного энкодера."""
        if encoder.endswith("_nvenc"):
            return "Аппаратный энкодер NVIDIA NVENC: быстрее CPU, но качество на том же битрейте обычно ниже libx264/libx265."
        if encoder.endswith("_qsv"):
            return "Intel Quick Sync Video: быстрое аппаратное кодирование на встроенной графике Intel."
        if encoder.endswith("_amf"):
            return "AMD AMF: аппаратное кодирование на GPU AMD."
        if encoder.endswith("_vaapi"):
            return "VAAPI: аппаратное ускорение Linux (Intel/AMD)."
        if encoder.startswith("libx264"):
            return "Программный энкодер H.264, лучший баланс качества и совместимости."
        if encoder.startswith("libx265"):
            return "Программный энкодер HEVC: меньше размер файла при том же качестве, но кодирует медленнее H.264."
        return ""

    # ------------------------------------------------------------------
    def _register_defaults(self) -> None:
        self.register(
            "crf",
            HelpEntry(
                brief="Constant Rate Factor — управляет качеством/размером.",
                detailed=(
                    "Меньшее значение — выше качество и больше размер файла. "
                    "Диапазон обычно 0–51 (H.264/HEVC) или 0–63 (AV1/VP9). "
                    "Разумные значения: 18–28."
                ),
            ),
        )
        self.register(
            "preset",
            HelpEntry(
                brief="Скорость кодирования в обмен на эффективность сжатия.",
                detailed=(
                    "От ultrafast (быстро, больше размер) до veryslow "
                    "(медленно, меньше размер при том же качестве)."
                ),
            ),
        )
        self.register(
            "bitrate",
            HelpEntry(
                brief="Целевой средний битрейт потока.",
                detailed="Используется вместо CRF, когда важен предсказуемый размер файла.",
            ),
        )
        self.register(
            "resolution",
            HelpEntry(
                brief="Разрешение выходного видео.",
                detailed="При включённом сохранении пропорций высота подбирается автоматически.",
                warning="Увеличение разрешения (апскейл) не улучшает исходное качество.",
            ),
        )
        self.register(
            "fps",
            HelpEntry(
                brief="Частота кадров результата.",
                detailed="Оставьте пустым, чтобы сохранить частоту кадров исходника.",
            ),
        )
        self.register(
            "hwaccel",
            HelpEntry(
                brief="Аппаратное ускорение кодирования/декодирования.",
                warning="Доступность зависит от GPU и драйверов, а не только от сборки FFmpeg.",
            ),
        )
        self.register(
            "stream_copy",
            HelpEntry(
                brief="Stream Copy — копирование потока без перекодирования.",
                detailed="Работает быстро и без потерь качества, но требует совместимости кодека с контейнером.",
            ),
        )
        self.register(
            "auto_hardware",
            HelpEntry(
                brief="Автоматически использовать видеоускоритель (GPU) для кодирования, если он найден.",
                detailed=(
                    "NVIDIA -> NVENC, AMD -> AMF/VAAPI, Intel -> Quick Sync/VAAPI. "
                    "Кодирование на GPU обычно быстрее, но при том же битрейте/CRF "
                    "качество чуть ниже программных энкодеров (libx264/libx265)."
                ),
                warning=(
                    "Влияет только на строки «Авто» и «Авто (по настройкам)» в списке кодека. "
                    "Если выбрана конкретная строка с видеокартой или процессором — используется именно она."
                ),
            ),
        )
        self.register(
            "default_crf",
            HelpEntry(
                brief="Качество по умолчанию для новых заданий сжатия/конвертации.",
                detailed=(
                    "Значение CRF, которое подставляется на вкладках «Сжатие» и «Конвертация». "
                    "Чем меньше число — тем выше качество и больше размер файла."
                ),
            ),
        )
        self.register(
            "show_tooltips",
            HelpEntry(brief="Показывать всплывающие подсказки при наведении на параметры."),
        )
        self.register(
            "trim_accurate",
            HelpEntry(
                brief="Точная обрезка перекодирует видео для кадровой точности.",
                detailed="Быстрая обрезка режет по ближайшему ключевому кадру и работает почти мгновенно.",
            ),
        )


registry = HelpRegistry()
