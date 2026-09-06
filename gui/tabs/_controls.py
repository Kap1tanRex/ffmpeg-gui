"""Общие панели настроек видео/аудио для вкладок Сжатие и Конвертация.

Вынесено в отдельный модуль, чтобы не дублировать построение виджетов
между `compress_tab.py` и `convert_tab.py` — обе вкладки работают с одной
и той же моделью :class:`VideoOptions`/`AudioOptions`.

Кодеки выбираются не по «логическому» имени (h264/hevc/...), а построчно —
каждый пункт списка явно называет устройство (Процессор / видеокарта +
производитель), на котором этот вариант будет выполняться. По умолчанию
показаны только часто используемые кодеки (раздел «Кодеки» в настройках
переключает расширенный список — :attr:`Settings.show_all_codecs`).
"""

from __future__ import annotations

from collections.abc import Callable

import customtkinter as ctk

from ...core.help_registry import crf_quality
from ...core.models import AudioOptions, VideoOptions
from ..theming import font, pair
from ..widgets.surface import Card, muted, severity_color
from ..widgets.tooltip import attach_help

AUTO_LABEL = "Авто — подобрать автоматически"

# Режим дорожки называется словами, а не значением FFmpeg: наружу панель
# по-прежнему отдаёт "encode" / "copy" / "none" (см. :meth:`VideoPanel.mode`).
_MODE_LABELS: dict[str, str] = {
    "encode": "Перекодировать",
    "copy": "Копировать без перекодирования",
    "none": "Убрать дорожку",
}
_MODE_BY_LABEL: dict[str, str] = {label: mode for mode, label in _MODE_LABELS.items()}

#: Ширина колонки подписей внутри панели — строки выстраиваются в таблицу.
_LABEL_WIDTH = 168

# Раздел «часто используемые»: базовый список — только H.264/H.265 и то,
# что видеокарта умеет ускорять. Остальное доступно в расширенном режиме.
_BASIC_VIDEO_CODECS: tuple[str, ...] = ("h264", "hevc")

_VIDEO_CODEC_LABELS: dict[str, str] = {
    "h264": "H.264",
    "hevc": "H.265 / HEVC",
    "av1": "AV1",
    "vp9": "VP9",
    "vp8": "VP8",
    "mpeg4": "MPEG-4",
    "mpeg2video": "MPEG-2",
    "prores": "ProRes",
    "mjpeg": "MJPEG",
    "flv1": "FLV1",
    "wmv2": "WMV2",
    "theora": "Theora",
}

# Два способа задать качество, между которыми выбирает пользователь.
# Остальные режимы модели (bitrate, qp, lossless) панель не показывает —
# они остаются доступны через профили и экспертные параметры.
_QUALITY_LABELS: dict[str, str] = {
    "crf": "Постоянное качество (CRF)",
    "size": "Целевой размер файла",
}
_QUALITY_BY_LABEL: dict[str, str] = {label: mode for mode, label in _QUALITY_LABELS.items()}

_BASIC_AUDIO_CODECS: tuple[str, ...] = ("aac", "mp3", "opus", "ac3", "flac")

_AUDIO_CODEC_LABELS: dict[str, str] = {
    "aac": "AAC",
    "mp3": "MP3",
    "opus": "Opus",
    "ac3": "AC3",
    "eac3": "E-AC3",
    "flac": "FLAC (без потерь)",
    "alac": "ALAC (без потерь)",
    "vorbis": "Vorbis",
    "pcm_s16le": "PCM 16-бит",
    "wmav2": "WMA",
}

CodecChoice = tuple[str, "str | None"]  # (логический кодек, конкретный энкодер | None)


def _caption(master, text: str, row: int) -> ctk.CTkLabel:
    """Подпись параметра в левой колонке панели."""
    label = ctk.CTkLabel(master, text=text, anchor="w", width=_LABEL_WIDTH, font=font("body"))
    label.grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
    return label


def _device_label(encoder) -> str:
    if encoder.is_hardware:
        return f"Видеокарта: {encoder.hardware_vendor}"
    return "Процессор (CPU)"


def build_video_codec_choices(
    caps, show_all: bool, hw_suffixes: "tuple[str, ...] | None" = None
) -> dict[str, CodecChoice]:
    """Строит подписанные варианты выбора видеокодека.

    Каждая строка явно называет устройство исполнения, например:
    «H.264 — Процессор (CPU) · libx264» или
    «H.265 / HEVC — Видеокарта: NVIDIA NVENC · hevc_nvenc».

    ``hw_suffixes`` — суффиксы энкодеров, под которые в системе действительно
    есть видеокарта (``("nvenc",)`` и т.п.). Аппаратные варианты вне этого
    набора не показываются: в любой сборке FFmpeg для Windows скомпилированы
    все энкодеры сразу, и без такой проверки на машине с одним лишь Intel
    предлагался бы NVENC, падающий с «No capable devices found».
    ``None`` — не фильтровать (видеокарты не определены).
    """
    choices: dict[str, CodecChoice] = {AUTO_LABEL: ("auto", None)}
    available = {c.name for c in caps.encoding_codecs("video")}
    if show_all:
        ordered = list(_BASIC_VIDEO_CODECS) + sorted(
            name for name in available if name not in _BASIC_VIDEO_CODECS
        )
    else:
        ordered = [name for name in _BASIC_VIDEO_CODECS if name in available]

    for codec_name in ordered:
        encoders = caps.encoders_for_codec(codec_name)
        if not encoders:
            continue
        label = _VIDEO_CODEC_LABELS.get(codec_name, codec_name.upper())
        choices[f"{label} — Авто (по настройкам)"] = (codec_name, None)
        software = [e for e in encoders if not e.is_hardware]
        hardware = sorted((e for e in encoders if e.is_hardware), key=lambda e: e.hardware_vendor)
        probe = getattr(caps, "hardware_probe", None)
        if probe:
            # Проба точнее отбора по производителю: она ловит и случай, когда
            # видеокарта есть, а энкодер на ней не заводится — старый драйвер,
            # сеанс без доступа к GPU, отключённая дискретная графика.
            hardware = [e for e in hardware if not probe.get(e.name, "")]
        elif hw_suffixes:
            hardware = [
                e for e in hardware if any(e.name.endswith(f"_{s}") for s in hw_suffixes)
            ]
        for enc in software:
            choices[f"{label} — Процессор (CPU) · {enc.name}"] = (codec_name, enc.name)
        for enc in hardware:
            choices[f"{label} — {_device_label(enc)} · {enc.name}"] = (codec_name, enc.name)
    return choices


def build_audio_codec_choices(caps, show_all: bool) -> dict[str, CodecChoice]:
    """Аудио почти всегда исполняется на процессоре — подписи короче."""
    choices: dict[str, CodecChoice] = {AUTO_LABEL: ("auto", None)}
    available = {c.name for c in caps.encoding_codecs("audio")}
    if show_all:
        ordered = list(_BASIC_AUDIO_CODECS) + sorted(
            name for name in available if name not in _BASIC_AUDIO_CODECS
        )
    else:
        ordered = [name for name in _BASIC_AUDIO_CODECS if name in available]

    for codec_name in ordered:
        encoders = caps.encoders_for_codec(codec_name)
        if not encoders:
            continue
        label = _AUDIO_CODEC_LABELS.get(codec_name, codec_name.upper())
        if len(encoders) == 1:
            choices[label] = (codec_name, encoders[0].name)
        else:
            for enc in encoders:
                choices[f"{label} · {enc.name}"] = (codec_name, enc.name)
    return choices


class VideoPanel(Card):
    """Раздел 36: режим, кодек, качество, preset, разрешение, FPS.

    Оформлена карточкой с заголовком: параметры видео и аудио — два
    равноправных блока раздела, а не сплошной столбец подписей.
    """

    def __init__(self, master, app, show_resolution: bool = True) -> None:
        super().__init__(master, title="Видео")
        self.app = app
        body = self.label_grid()
        self._choices: dict[str, CodecChoice] = {AUTO_LABEL: ("auto", None)}
        # Раздел «Предустановки» (раздел «Конвертация»): вызывается только
        # при непосредственном действии пользователя в виджете — не при
        # программных set_from()/refresh_encoders() — чтобы раздел мог
        # понять, что настройки разошлись с выбранной предустановкой.
        self.on_user_change: "Callable[[], None] | None" = None
        # Оценка размера должна пересчитываться и при программной установке
        # параметров — при смене файла, применении профиля или предустановки,
        # а не только когда пользователь сам щёлкнул по виджету.
        self.on_changed: "Callable[[], None] | None" = None

        row = 0
        _caption(body, "Режим", row)
        self.mode_menu = ctk.CTkOptionMenu(
            body,
            values=list(_MODE_LABELS.values()),
            command=lambda _v: self._on_user_mode_changed(),
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.mode_menu.set(_MODE_LABELS["encode"])
        self.mode_menu.grid(row=row, column=1, sticky="ew", pady=3)
        attach_help(self.mode_menu, app, "stream_copy")
        row += 1

        _caption(body, "Кодек", row)
        self.codec_menu = ctk.CTkOptionMenu(
            body,
            values=[AUTO_LABEL],
            command=lambda _v: (
                self._refresh_presets(),
                self._refresh_crf_quality(),
                self._notify_user_change(),
            ),
            width=320,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.codec_menu.grid(row=row, column=1, sticky="ew", pady=3)
        attach_help(self.codec_menu, app, "codec")
        row += 1

        _caption(body, "Качество", row)
        self.quality_menu = ctk.CTkOptionMenu(
            body,
            values=list(_QUALITY_LABELS.values()),
            command=lambda _v: self._on_user_quality_changed(),
            width=320,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.quality_menu.set(_QUALITY_LABELS["crf"])
        self.quality_menu.grid(row=row, column=1, sticky="ew", pady=3)
        attach_help(self.quality_menu, app, "quality_mode")
        row += 1

        self._crf_caption = _caption(body, "Значение CRF", row)
        quality_frame = ctk.CTkFrame(body, fg_color="transparent")
        quality_frame.grid(row=row, column=1, sticky="ew", pady=3)
        self._crf_frame = quality_frame
        quality_frame.grid_columnconfigure(0, weight=1)
        self.crf_slider = ctk.CTkSlider(
            quality_frame,
            from_=0,
            to=51,
            number_of_steps=51,
            command=lambda v: (self._on_crf_slider(v), self._notify_user_change()),
        )
        default_crf = getattr(app.settings, "default_crf", 23.0)
        self.crf_slider.set(default_crf)
        self.crf_slider.grid(row=0, column=0, sticky="ew")
        self.crf_value_label = ctk.CTkLabel(
            quality_frame,
            text=str(int(default_crf)),
            width=34,
            font=font("body", bold=True),
            text_color=pair("accent"),
        )
        self.crf_value_label.grid(row=0, column=1, padx=(8, 0))
        attach_help(self.crf_slider, app, "crf")
        attach_help(self.crf_value_label, app, "crf")
        row += 1

        # Само число ни о чём не говорит: 23 — это много или мало, видно только
        # по результату. Строка ниже отвечает на этот вопрос словами и цветом.
        self._crf_hint = muted(body, "")
        self._crf_hint.grid(row=row, column=1, sticky="w", pady=(0, 2))
        row += 1

        # Строка целевого размера занимает то же место, что и CRF: показана
        # ровно одна из двух — какая, решает выбор выше.
        self._size_caption = _caption(body, "Размер файла", row)
        size_frame = ctk.CTkFrame(body, fg_color="transparent")
        size_frame.grid(row=row, column=1, sticky="w", pady=3)
        self._size_frame = size_frame
        self.size_entry = ctk.CTkEntry(
            size_frame, width=78, placeholder_text="100", font=font("small")
        )
        self.size_entry.grid(row=0, column=0)
        self.size_entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
        ctk.CTkLabel(size_frame, text="МиБ", font=font("small")).grid(
            row=0, column=1, padx=(6, 14)
        )
        self.two_pass_check = ctk.CTkCheckBox(
            size_frame,
            text="два прохода",
            command=self._notify_user_change,
            font=font("small"),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.two_pass_check.select()
        self.two_pass_check.grid(row=0, column=2)
        attach_help(self.size_entry, app, "target_size")
        attach_help(self.two_pass_check, app, "two_pass")
        row += 1

        self._size_hint = muted(body, "Битрейт подбирается под указанный размер")
        self._size_hint.grid(row=row, column=1, sticky="w", pady=(0, 2))
        row += 1
        self._apply_quality_visibility()
        self._refresh_crf_quality()

        _caption(body, "Preset", row)
        self.preset_menu = ctk.CTkOptionMenu(
            body,
            values=["medium"],
            command=lambda _v: self._notify_user_change(),
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.preset_menu.grid(row=row, column=1, sticky="w", pady=3)
        attach_help(self.preset_menu, app, "preset")
        row += 1

        if show_resolution:
            _caption(body, "Разрешение", row)
            res_frame = ctk.CTkFrame(body, fg_color="transparent")
            res_frame.grid(row=row, column=1, sticky="w", pady=3)
            self.width_entry = ctk.CTkEntry(
                res_frame, width=78, placeholder_text="ширина", font=font("small")
            )
            self.width_entry.grid(row=0, column=0)
            self.width_entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
            ctk.CTkLabel(res_frame, text="×", text_color=pair("fg.secondary")).grid(
                row=0, column=1, padx=6
            )
            self.height_entry = ctk.CTkEntry(
                res_frame, width=78, placeholder_text="высота", font=font("small")
            )
            self.height_entry.grid(row=0, column=2)
            self.height_entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
            attach_help(res_frame, app, "resolution")
            row += 1

            _caption(body, "FPS", row)
            self.fps_entry = ctk.CTkEntry(
                body, width=90, placeholder_text="авто", font=font("small")
            )
            self.fps_entry.grid(row=row, column=1, sticky="w", pady=3)
            self.fps_entry.bind("<KeyRelease>", lambda _e: self._notify_changed(), add="+")
            attach_help(self.fps_entry, app, "fps")
            row += 1
        else:
            self.width_entry = None
            self.height_entry = None
            self.fps_entry = None

        self._on_mode_changed()

    # -- режим дорожки ------------------------------------------------------
    def mode(self) -> str:
        """Режим значением FFmpeg: ``encode`` / ``copy`` / ``none``."""
        return _MODE_BY_LABEL.get(self.mode_menu.get(), "encode")

    def set_mode(self, mode: str) -> None:
        self.mode_menu.set(_MODE_LABELS.get(mode, _MODE_LABELS["encode"]))
        self._notify_changed()

    def _on_mode_changed(self) -> None:
        enabled = self.mode() == "encode"
        state = "normal" if enabled else "disabled"
        for widget in (
            self.codec_menu,
            self.quality_menu,
            self.crf_slider,
            self.preset_menu,
            self.size_entry,
            self.two_pass_check,
        ):
            widget.configure(state=state)

    def _on_user_mode_changed(self) -> None:
        self._on_mode_changed()
        self._notify_user_change()

    # -- способ задания качества --------------------------------------------
    def quality_mode(self) -> str:
        """``crf`` или ``size`` — значением модели, а не подписью."""
        return _QUALITY_BY_LABEL.get(self.quality_menu.get(), "crf")

    def _apply_quality_visibility(self) -> None:
        by_size = self.quality_mode() == "size"
        for widget in (self._crf_caption, self._crf_frame, self._crf_hint):
            widget.grid_remove() if by_size else widget.grid()
        for widget in (self._size_caption, self._size_frame, self._size_hint):
            widget.grid() if by_size else widget.grid_remove()

    def _on_user_quality_changed(self) -> None:
        self._apply_quality_visibility()
        self._notify_user_change()

    def _notify_user_change(self) -> None:
        if self.on_user_change is not None:
            self.on_user_change()
        self._notify_changed()

    def _notify_changed(self) -> None:
        if self.on_changed is not None:
            self.on_changed()

    def _on_crf_slider(self, value: float) -> None:
        self.crf_value_label.configure(text=str(int(value)))
        self._refresh_crf_quality()

    def _refresh_crf_quality(self) -> None:
        """Подпись качества под ползунком: что это число значит для картинки.

        Шкала зависит от кодека — у AV1 и VP9 она до 63, а не до 51, и то же
        число там означает совсем другое качество.
        """
        codec, _encoder = self._current_choice()
        label, severity = crf_quality(self.crf_slider.get(), codec)
        self._crf_hint.configure(text=label, text_color=severity_color(severity))

    def refresh_encoders(self) -> None:
        caps = self.app.capabilities
        show_all = bool(getattr(self.app.settings, "show_all_codecs", False))
        # Полный список всегда держим под рукой (для восстановления выбора
        # из профилей/заданий, даже если он сейчас скрыт базовым режимом).
        self._choices = build_video_codec_choices(caps, show_all=True)
        # Расширенный список («показать все кодеки») заодно снимает отбор по
        # видеокарте — осознанный выход для нераспознанного железа.
        suffixes = None
        if not show_all and hasattr(self.app, "available_hw_suffixes"):
            suffixes = self.app.available_hw_suffixes() or None
        visible = build_video_codec_choices(caps, show_all=show_all, hw_suffixes=suffixes)
        values = list(visible.keys())

        current = self.codec_menu.get()
        self.codec_menu.configure(values=values)
        if current in values:
            self.codec_menu.set(current)
        else:
            fallback = self._choices.get(current)
            label = self._label_for(*fallback) if fallback else AUTO_LABEL
            if label not in values:
                values = values + [label]
                self.codec_menu.configure(values=values)
            self.codec_menu.set(label)
        self._refresh_presets()
        self._notify_changed()

    def apply_default_crf(self, value: float) -> None:
        """Раздел настроек: подставить новое CRF по умолчанию (для уже открытых разделов)."""
        self.crf_slider.set(value)
        self.crf_value_label.configure(text=str(int(value)))
        self._refresh_crf_quality()
        self._notify_changed()

    def _current_choice(self) -> CodecChoice:
        return self._choices.get(self.codec_menu.get(), ("auto", None))

    def _label_for(self, codec: str, encoder: str | None) -> str:
        if not codec or codec == "auto":
            return AUTO_LABEL
        if encoder:
            for label, (c, e) in self._choices.items():
                if c == codec and e == encoder:
                    return label
        for label, (c, e) in self._choices.items():
            if c == codec and e is None:
                return label
        return AUTO_LABEL

    def _refresh_presets(self) -> None:
        codec, encoder = self._current_choice()
        if encoder is None and codec not in ("auto", "", None) and hasattr(self.app, "compat"):
            prefer = self.app.preferred_hw_suffixes() if hasattr(self.app, "preferred_hw_suffixes") else ()
            encoder = self.app.compat.resolve_encoder(codec, "video", prefer)
        presets: list[str] = []
        if encoder and hasattr(self.app, "capability_manager"):
            presets = self.app.capability_manager.encoder_presets(encoder)
        if not presets:
            presets = ["ultrafast", "fast", "medium", "slow", "veryslow"]
        current = self.preset_menu.get()
        self.preset_menu.configure(values=presets)
        if current not in presets:
            self.preset_menu.set("medium" if "medium" in presets else presets[0])

    def set_from(self, video: VideoOptions) -> None:
        self.set_mode(video.mode)
        label = self._label_for(video.codec or "auto", video.encoder)
        values = list(self.codec_menu.cget("values"))
        if label not in values:
            values = values + [label]
            self.codec_menu.configure(values=values)
        self.codec_menu.set(label)
        # Режимы, которых нет в списке (bitrate, qp, lossless), показываем как
        # CRF: панель ими не управляет, но и молча ломать их не должна.
        self.quality_menu.set(_QUALITY_LABELS.get(video.quality_mode, _QUALITY_LABELS["crf"]))
        self.size_entry.delete(0, "end")
        if video.target_size_mb:
            self.size_entry.insert(0, f"{video.target_size_mb:g}")
        self.two_pass_check.select() if video.two_pass else self.two_pass_check.deselect()
        self._apply_quality_visibility()
        self.crf_slider.set(video.crf if video.crf is not None else 23)
        self.crf_value_label.configure(text=str(int(video.crf if video.crf is not None else 23)))
        self._refresh_crf_quality()
        if video.preset:
            self.preset_menu.set(video.preset)
        if self.width_entry is not None:
            self.width_entry.delete(0, "end")
            if video.width:
                self.width_entry.insert(0, str(video.width))
            self.height_entry.delete(0, "end")
            if video.height:
                self.height_entry.insert(0, str(video.height))
            self.fps_entry.delete(0, "end")
            if video.fps:
                self.fps_entry.insert(0, str(video.fps))
        self._on_mode_changed()
        self._notify_changed()

    def get_options(self) -> VideoOptions:
        def _int_or_none(entry) -> int | None:
            if entry is None:
                return None
            text = entry.get().strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                return None

        def _float_or_none(entry) -> float | None:
            if entry is None:
                return None
            text = entry.get().strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None

        codec, encoder = self._current_choice()
        quality_mode = self.quality_mode()
        by_size = quality_mode == "size"
        return VideoOptions(
            mode=self.mode(),
            codec=codec,
            encoder=encoder,
            quality_mode=quality_mode,
            crf=self.crf_slider.get(),
            target_size_mb=_float_or_none(self.size_entry) if by_size else None,
            two_pass=bool(self.two_pass_check.get()) if by_size else False,
            preset=self.preset_menu.get(),
            width=_int_or_none(self.width_entry),
            height=_int_or_none(self.height_entry),
            fps=_float_or_none(self.fps_entry),
        )


class AudioPanel(Card):
    """Раздел 37: режим, кодек, битрейт, каналы."""

    def __init__(self, master, app) -> None:
        super().__init__(master, title="Аудио")
        self.app = app
        body = self.label_grid()
        self._choices: dict[str, CodecChoice] = {AUTO_LABEL: ("auto", None)}
        # См. VideoPanel.on_user_change — тот же приём для раздела «Конвертация».
        self.on_user_change: "Callable[[], None] | None" = None
        self.on_changed: "Callable[[], None] | None" = None

        row = 0
        _caption(body, "Режим", row)
        self.mode_menu = ctk.CTkOptionMenu(
            body,
            values=list(_MODE_LABELS.values()),
            command=lambda _v: self._on_user_mode_changed(),
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.mode_menu.set(_MODE_LABELS["encode"])
        self.mode_menu.grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        _caption(body, "Кодек", row)
        self.codec_menu = ctk.CTkOptionMenu(
            body,
            values=[AUTO_LABEL],
            command=lambda _v: self._notify_user_change(),
            width=260,
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.codec_menu.grid(row=row, column=1, sticky="ew", pady=3)
        row += 1

        _caption(body, "Битрейт", row)
        self.bitrate_menu = ctk.CTkOptionMenu(
            body,
            values=["96k", "128k", "160k", "192k", "256k", "320k"],
            command=lambda _v: self._notify_user_change(),
            anchor="w",
            font=font("small"),
            dropdown_font=font("small"),
        )
        self.bitrate_menu.set("160k")
        self.bitrate_menu.grid(row=row, column=1, sticky="w", pady=3)
        attach_help(self.bitrate_menu, app, "bitrate")
        row += 1

        _caption(body, "Громкость", row)
        self.normalize_check = ctk.CTkCheckBox(
            body,
            text="Привести к норме (EBU R128)",
            command=self._notify_user_change,
            font=font("small"),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.normalize_check.grid(row=row, column=1, sticky="w", pady=3)
        attach_help(self.normalize_check, app, "normalize")
        row += 1

        muted(body, "Одна громкость у разных записей").grid(
            row=row, column=1, sticky="w", pady=(0, 2)
        )
        row += 1

        self._on_mode_changed()

    # -- режим дорожки ------------------------------------------------------
    def mode(self) -> str:
        """Режим значением FFmpeg: ``encode`` / ``copy`` / ``none``."""
        return _MODE_BY_LABEL.get(self.mode_menu.get(), "encode")

    def set_mode(self, mode: str) -> None:
        self.mode_menu.set(_MODE_LABELS.get(mode, _MODE_LABELS["encode"]))
        self._notify_changed()

    def _on_mode_changed(self) -> None:
        enabled = self.mode() == "encode"
        state = "normal" if enabled else "disabled"
        for widget in (self.codec_menu, self.bitrate_menu, self.normalize_check):
            widget.configure(state=state)

    def _on_user_mode_changed(self) -> None:
        self._on_mode_changed()
        self._notify_user_change()

    def _notify_user_change(self) -> None:
        if self.on_user_change is not None:
            self.on_user_change()
        self._notify_changed()

    def _notify_changed(self) -> None:
        if self.on_changed is not None:
            self.on_changed()

    def refresh_encoders(self) -> None:
        caps = self.app.capabilities
        show_all = bool(getattr(self.app.settings, "show_all_codecs", False))
        self._choices = build_audio_codec_choices(caps, show_all=True)
        visible = build_audio_codec_choices(caps, show_all=show_all)
        values = list(visible.keys())

        current = self.codec_menu.get()
        self.codec_menu.configure(values=values)
        if current in values:
            self.codec_menu.set(current)
        else:
            fallback = self._choices.get(current)
            label = self._label_for(*fallback) if fallback else AUTO_LABEL
            if label not in values:
                values = values + [label]
                self.codec_menu.configure(values=values)
            self.codec_menu.set(label)
        self._notify_changed()

    def _current_choice(self) -> CodecChoice:
        return self._choices.get(self.codec_menu.get(), ("auto", None))

    def _label_for(self, codec: str, encoder: str | None) -> str:
        if not codec or codec == "auto":
            return AUTO_LABEL
        if encoder:
            for label, (c, e) in self._choices.items():
                if c == codec and e == encoder:
                    return label
        for label, (c, e) in self._choices.items():
            if c == codec:
                return label
        return AUTO_LABEL

    def set_from(self, audio: AudioOptions) -> None:
        self.set_mode(audio.mode)
        label = self._label_for(audio.codec or "auto", audio.encoder)
        values = list(self.codec_menu.cget("values"))
        if label not in values:
            values = values + [label]
            self.codec_menu.configure(values=values)
        self.codec_menu.set(label)
        if audio.bitrate:
            self.bitrate_menu.set(audio.bitrate)
        self.normalize_check.select() if audio.normalize else self.normalize_check.deselect()
        self._on_mode_changed()
        self._notify_changed()

    def get_options(self) -> AudioOptions:
        codec, encoder = self._current_choice()
        mode = self.mode()
        return AudioOptions(
            mode=mode,
            codec=codec,
            encoder=encoder,
            bitrate=self.bitrate_menu.get() if mode == "encode" else None,
            normalize=bool(self.normalize_check.get()) and mode == "encode",
        )
