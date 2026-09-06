"""Динамическое обнаружение возможностей конкретной сборки FFmpeg.

Разделы ТЗ: 8 (никаких статических SUPPORTED_CODECS), 9 (Capability Manager),
21 (Advanced Parameter Editor), 35 (аппаратное ускорение).

Единственный источник истины о кодеках — вывод самого FFmpeg.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_VERSION = 2  # 2: добавлена проба аппаратных энкодеров


# ---------------------------------------------------------------------------
# Модели (раздел 9)
# ---------------------------------------------------------------------------


@dataclass
class EncoderInfo:
    name: str
    description: str
    media_type: str  # video | audio | subtitle
    flags: list[str] = field(default_factory=list)
    codec: str = ""  # логический кодек, если удалось определить

    @property
    def is_hardware(self) -> bool:
        return any(tag in self.name for tag in HARDWARE_SUFFIXES)

    @property
    def hardware_vendor(self) -> str:
        for suffix, title in HARDWARE_SUFFIXES.items():
            if suffix in self.name:
                return title
        return "CPU"


@dataclass
class DecoderInfo:
    name: str
    description: str
    media_type: str
    flags: list[str] = field(default_factory=list)


@dataclass
class CodecInfo:
    name: str
    description: str
    media_type: str
    decoding: bool = False
    encoding: bool = False
    lossless: bool = False
    lossy: bool = False
    encoders: list[str] = field(default_factory=list)
    decoders: list[str] = field(default_factory=list)


@dataclass
class ContainerInfo:
    name: str
    description: str
    can_mux: bool
    can_demux: bool

    @property
    def names(self) -> list[str]:
        return [part for part in self.name.split(",") if part]


@dataclass
class FilterInfo:
    name: str
    description: str
    inputs: str = ""
    outputs: str = ""
    timeline_support: bool = False

    @property
    def media_type(self) -> str:
        if self.inputs.startswith("A") or self.outputs.startswith("A"):
            return "audio"
        if self.inputs.startswith("V") or self.outputs.startswith("V"):
            return "video"
        return "other"


@dataclass
class EncoderOption:
    """Раздел 21."""

    name: str
    type: str
    default: object = None
    min_value: float | None = None
    max_value: float | None = None
    choices: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def widget(self) -> str:
        """bool → checkbox, enum → combobox, int → spinbox, float → entry, str → entry."""
        if self.choices:
            return "combobox"
        return {
            "boolean": "checkbox",
            "int": "spinbox",
            "int64": "spinbox",
            "float": "entry",
            "double": "entry",
        }.get(self.type, "entry")


HARDWARE_SUFFIXES: dict[str, str] = {
    "_nvenc": "NVIDIA NVENC",
    "nvenc": "NVIDIA NVENC",
    "_qsv": "Intel Quick Sync",
    "_amf": "AMD AMF",
    "_vaapi": "VAAPI",
    "_videotoolbox": "VideoToolbox",
    "_v4l2m2m": "V4L2 M2M",
    "_mf": "Media Foundation",
    "_mediacodec": "MediaCodec",
    "_vulkan": "Vulkan",
    "_d3d12va": "Direct3D 12 VA",
    "_d3d11va": "Direct3D 11 VA",
    "_dxva2": "DXVA2",
    "_cuvid": "NVIDIA CUVID",
}

MEDIA_TYPE_BY_FLAG = {"V": "video", "A": "audio", "S": "subtitle"}

# Раздел 13. Шкала preset для энкодеров, объявляющих preset строкой.
STANDARD_PRESETS: tuple[str, ...] = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)


#: Строки, ради которых проба и делается: по ним видно, почему энкодер не
#: завёлся. Проверяются в этом порядке — от конкретной причины к общей.
_PROBE_MARKERS: tuple[str, ...] = (
    "minimum required nvidia driver",
    "driver does not support",
    "cannot load nvcuda",
    "cannot load nvencodeapi",
    "failed to open",
    "no capable devices found",
    "not supported",
    "no device available",
    "error creating a mfx session",
    "failed loading",
    "unknown encoder",
)


#: Строки отчёта, в которых слово «error» не означает ошибку.
_PROBE_NOISE: tuple[str, ...] = ("decode errors", "error concealment", "packets read")


def probe_reason(output: str) -> str:
    """Короткая причина отказа из подробного вывода FFmpeg.

    Вывод пробы — десятки строк; пользователю нужна одна, объясняющая отказ.
    """
    def clean(line: str) -> str:
        # Отрезаем служебный префикс вида «[h264_nvenc @ 0x...] ».
        return (line.split("] ", 1)[-1] if line.startswith("[") else line)[:160]

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for marker in _PROBE_MARKERS:
        for line in lines:
            if marker in line.lower():
                return clean(line)
    # Запасной разбор. «0 decode errors» — обычная строка отчёта, и слово
    # «error» в ней ничего не значит: такие строки пропускаем.
    for line in reversed(lines):
        lowered = line.lower()
        if any(word in lowered for word in _PROBE_NOISE):
            continue
        if any(word in lowered for word in ("error", "failed", "cannot", "could not", "unable")):
            cleaned = clean(line)
            # Для части энкодеров FFmpeg не сообщает ничего конкретнее этого.
            if cleaned.lower().startswith("conversion failed"):
                return "не поддерживается на этой машине"
            return cleaned
    return "энкодер не запустился"


@dataclass
class FFmpegCapabilities:
    """Полный снимок возможностей сборки."""

    ffmpeg_version: str = ""
    ffmpeg_path: str = ""
    build_configuration: str = ""
    encoders: dict[str, EncoderInfo] = field(default_factory=dict)
    decoders: dict[str, DecoderInfo] = field(default_factory=dict)
    codecs: dict[str, CodecInfo] = field(default_factory=dict)
    muxers: dict[str, ContainerInfo] = field(default_factory=dict)
    demuxers: dict[str, ContainerInfo] = field(default_factory=dict)
    filters: dict[str, FilterInfo] = field(default_factory=dict)
    pix_fmts: list[str] = field(default_factory=list)
    sample_fmts: list[str] = field(default_factory=list)
    channel_layouts: list[str] = field(default_factory=list)
    hwaccels: list[str] = field(default_factory=list)
    bsfs: list[str] = field(default_factory=list)
    #: Аппаратный энкодер -> "" если он действительно заработал на этой
    #: машине, иначе краткая причина отказа. Пустой словарь означает «проба
    #: не проводилась», а не «всё сломано».
    hardware_probe: dict[str, str] = field(default_factory=dict)

    # ---- запросы --------------------------------------------------------
    def has_encoder(self, name: str) -> bool:
        return name in self.encoders

    def has_decoder(self, name: str) -> bool:
        return name in self.decoders

    def has_muxer(self, name: str) -> bool:
        return name in self.muxers or any(name in c.names for c in self.muxers.values())

    def has_filter(self, name: str) -> bool:
        return name in self.filters

    def encoders_for_codec(self, codec: str) -> list[EncoderInfo]:
        """Все энкодеры логического кодека: libx264, h264_nvenc, h264_qsv ..."""
        codec_info = self.codecs.get(codec)
        names: list[str] = list(codec_info.encoders) if codec_info else []
        if not names and codec_info is None:
            names = [codec] if codec in self.encoders else []
        for encoder in self.encoders.values():
            if encoder.codec == codec and encoder.name not in names:
                names.append(encoder.name)
        result = [self.encoders[name] for name in names if name in self.encoders]
        result.sort(key=lambda e: (e.is_hardware, e.name))
        return result

    def encoding_codecs(self, media_type: str) -> list[CodecInfo]:
        items = [
            codec
            for codec in self.codecs.values()
            if codec.media_type == media_type and codec.encoding and self.encoders_for_codec(codec.name)
        ]
        items.sort(key=lambda c: c.name)
        return items

    def muxer_list(self) -> list[ContainerInfo]:
        return sorted(self.muxers.values(), key=lambda c: c.name)

    def hardware_encoders(self) -> list[EncoderInfo]:
        return [enc for enc in self.encoders.values() if enc.is_hardware]

    # ---- сериализация ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "cache_version": CACHE_VERSION,
            "ffmpeg_version": self.ffmpeg_version,
            "ffmpeg_path": self.ffmpeg_path,
            "build_configuration": self.build_configuration,
            "encoders": {k: asdict(v) for k, v in self.encoders.items()},
            "decoders": {k: asdict(v) for k, v in self.decoders.items()},
            "codecs": {k: asdict(v) for k, v in self.codecs.items()},
            "muxers": {k: asdict(v) for k, v in self.muxers.items()},
            "demuxers": {k: asdict(v) for k, v in self.demuxers.items()},
            "filters": {k: asdict(v) for k, v in self.filters.items()},
            "pix_fmts": self.pix_fmts,
            "sample_fmts": self.sample_fmts,
            "channel_layouts": self.channel_layouts,
            "hwaccels": self.hwaccels,
            "bsfs": self.bsfs,
            "hardware_probe": self.hardware_probe,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FFmpegCapabilities":
        caps = cls(
            ffmpeg_version=data.get("ffmpeg_version", ""),
            ffmpeg_path=data.get("ffmpeg_path", ""),
            build_configuration=data.get("build_configuration", ""),
            pix_fmts=list(data.get("pix_fmts", [])),
            sample_fmts=list(data.get("sample_fmts", [])),
            channel_layouts=list(data.get("channel_layouts", [])),
            hwaccels=list(data.get("hwaccels", [])),
            bsfs=list(data.get("bsfs", [])),
            hardware_probe=dict(data.get("hardware_probe", {})),
        )
        caps.encoders = {k: EncoderInfo(**v) for k, v in data.get("encoders", {}).items()}
        caps.decoders = {k: DecoderInfo(**v) for k, v in data.get("decoders", {}).items()}
        caps.codecs = {k: CodecInfo(**v) for k, v in data.get("codecs", {}).items()}
        caps.muxers = {k: ContainerInfo(**v) for k, v in data.get("muxers", {}).items()}
        caps.demuxers = {k: ContainerInfo(**v) for k, v in data.get("demuxers", {}).items()}
        caps.filters = {k: FilterInfo(**v) for k, v in data.get("filters", {}).items()}
        return caps


# ---------------------------------------------------------------------------
# Парсеры вывода FFmpeg
# ---------------------------------------------------------------------------

_CODEC_LINE = re.compile(r"^\s*(?P<flags>[A-Z.]{6})\s+(?P<name>\S+)\s*(?P<desc>.*)$")
_ENCODER_LINE = re.compile(r"^\s*(?P<flags>[A-Za-z.]{6})\s+(?P<name>\S+)\s+(?P<desc>.*)$")
_FORMAT_LINE = re.compile(r"^\s*(?P<flags>[DE ]{1,3})\s+(?P<name>\S+)\s+(?P<desc>.*)$")
_FILTER_LINE = re.compile(
    r"^\s*(?P<flags>[TSC.]{3})\s+(?P<name>\S+)\s+(?P<io>\S+)\s+(?P<desc>.*)$"
)
_CODEC_REF = re.compile(r"\(codec (?P<codec>[^)]+)\)")
_ENCODERS_REF = re.compile(r"\(encoders: (?P<list>[^)]*)\)")
_DECODERS_REF = re.compile(r"\(decoders: (?P<list>[^)]*)\)")


def _strip_header(text: str) -> list[str]:
    """Отбрасывает шапку до строки с '------'."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if set(line.strip()) == {"-"} and len(line.strip()) > 3:
            return lines[index + 1 :]
    return lines


def parse_encoders(text: str) -> dict[str, EncoderInfo]:
    result: dict[str, EncoderInfo] = {}
    for line in _strip_header(text):
        if not line.strip():
            continue
        match = _ENCODER_LINE.match(line)
        if not match:
            continue
        flags = match.group("flags")
        media_type = MEDIA_TYPE_BY_FLAG.get(flags[0].upper())
        if media_type is None:
            continue
        name = match.group("name")
        description = match.group("desc").strip()
        codec_match = _CODEC_REF.search(description)
        codec = codec_match.group("codec").strip() if codec_match else _guess_codec(name)
        result[name] = EncoderInfo(
            name=name,
            description=description,
            media_type=media_type,
            flags=[f for f in flags if f != "."],
            codec=codec,
        )
    return result


def parse_decoders(text: str) -> dict[str, DecoderInfo]:
    result: dict[str, DecoderInfo] = {}
    for line in _strip_header(text):
        if not line.strip():
            continue
        match = _ENCODER_LINE.match(line)
        if not match:
            continue
        media_type = MEDIA_TYPE_BY_FLAG.get(match.group("flags")[0].upper())
        if media_type is None:
            continue
        result[match.group("name")] = DecoderInfo(
            name=match.group("name"),
            description=match.group("desc").strip(),
            media_type=media_type,
            flags=[f for f in match.group("flags") if f != "."],
        )
    return result


def parse_codecs(text: str) -> dict[str, CodecInfo]:
    result: dict[str, CodecInfo] = {}
    for line in _strip_header(text):
        if not line.strip():
            continue
        match = _CODEC_LINE.match(line)
        if not match:
            continue
        flags = match.group("flags")
        media_type = MEDIA_TYPE_BY_FLAG.get(flags[2].upper())
        if media_type is None:
            continue
        description = match.group("desc").strip()
        encoders_match = _ENCODERS_REF.search(description)
        decoders_match = _DECODERS_REF.search(description)
        name = match.group("name")
        encoders = encoders_match.group("list").split() if encoders_match else []
        decoders = decoders_match.group("list").split() if decoders_match else []
        clean_desc = _ENCODERS_REF.sub("", _DECODERS_REF.sub("", description)).strip()
        result[name] = CodecInfo(
            name=name,
            description=clean_desc,
            media_type=media_type,
            decoding=flags[0] == "D",
            encoding=flags[1] == "E",
            lossy=flags[4] == "L",
            lossless=flags[5] == "S",
            encoders=encoders,
            decoders=decoders,
        )
    return result


def parse_formats(text: str) -> tuple[dict[str, ContainerInfo], dict[str, ContainerInfo]]:
    """Разбирает `-formats`, `-muxers` или `-demuxers`."""
    muxers: dict[str, ContainerInfo] = {}
    demuxers: dict[str, ContainerInfo] = {}
    for line in _strip_header(text):
        if not line.strip():
            continue
        raw_flags = line[:4]
        if not set(raw_flags.strip()) <= {"D", "E"} or not raw_flags.strip():
            continue
        rest = line[4:].strip()
        if not rest:
            continue
        parts = rest.split(None, 1)
        name = parts[0]
        description = parts[1].strip() if len(parts) > 1 else ""
        can_demux = "D" in raw_flags
        can_mux = "E" in raw_flags
        info = ContainerInfo(
            name=name, description=description, can_mux=can_mux, can_demux=can_demux
        )
        if can_mux:
            muxers[name] = info
        if can_demux:
            demuxers[name] = info
    return muxers, demuxers


def parse_filters(text: str) -> dict[str, FilterInfo]:
    result: dict[str, FilterInfo] = {}
    for line in _strip_header(text):
        if not line.strip():
            continue
        match = _FILTER_LINE.match(line)
        if not match:
            continue
        io_spec = match.group("io")
        inputs, _, outputs = io_spec.partition("->")
        result[match.group("name")] = FilterInfo(
            name=match.group("name"),
            description=match.group("desc").strip(),
            inputs=inputs,
            outputs=outputs,
            timeline_support="T" in match.group("flags"),
        )
    return result


def parse_pix_fmts(text: str) -> list[str]:
    result: list[str] = []
    for line in _strip_header(text):
        parts = line.split()
        if len(parts) >= 2 and set(parts[0]) <= set("IOHPB."):
            result.append(parts[1])
    return result


def parse_sample_fmts(text: str) -> list[str]:
    result: list[str] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            result.append(parts[0])
    return result


def parse_layouts(text: str) -> list[str]:
    result: list[str] = []
    in_layouts = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Standard channel layouts"):
            in_layouts = True
            continue
        if not stripped:
            continue
        if in_layouts:
            parts = stripped.split()
            if len(parts) >= 2 and parts[0] != "NAME":
                result.append(parts[0])
    return result


def parse_simple_list(text: str) -> list[str]:
    """Для `-hwaccels` и `-bsfs`: пропускает заголовок, берёт по слову в строке."""
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.endswith(":") or " " in stripped:
            continue
        result.append(stripped)
    return result


def parse_encoder_options(text: str) -> list[EncoderOption]:
    """Разбирает `ffmpeg -h encoder=NAME` (раздел 21)."""
    options: list[EncoderOption] = []
    current: EncoderOption | None = None
    option_re = re.compile(
        r"^\s{2}-(?P<name>[\w:.\-]+)\s+<(?P<type>[\w]+)>\s+(?P<flags>\S+)\s*(?P<desc>.*)$"
    )
    choice_re = re.compile(r"^\s{4,}(?P<name>\S+)\s+(?P<value>[-\w.]+)?\s*(?P<flags>[EDVAS.]{11})?\s*(?P<desc>.*)$")
    range_re = re.compile(r"\(from (?P<min>[-\w.+]+) to (?P<max>[-\w.+]+)\)")
    default_re = re.compile(r"\(default (?P<default>.+?)\)\s*$")

    for line in text.splitlines():
        if not line.strip():
            continue
        match = option_re.match(line)
        if match:
            description = match.group("desc").strip()
            min_value = max_value = None
            range_match = range_re.search(description)
            if range_match:
                min_value = _to_float(range_match.group("min"))
                max_value = _to_float(range_match.group("max"))
                description = range_re.sub("", description).strip()
            default_value: object = None
            default_match = default_re.search(description)
            if default_match:
                default_value = default_match.group("default").strip().strip('"')
                description = default_re.sub("", description).strip()
            current = EncoderOption(
                name=match.group("name"),
                type=match.group("type"),
                default=default_value,
                min_value=min_value,
                max_value=max_value,
                description=description,
            )
            options.append(current)
            continue
        if current is not None and line.startswith("     "):
            choice = choice_re.match(line)
            if choice and choice.group("name") not in {"-"}:
                current.choices.append(choice.group("name"))
    return options


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _guess_codec(encoder_name: str) -> str:
    """Грубая эвристика на случай, если FFmpeg не указал '(codec ...)'."""
    known = {
        "libx264": "h264",
        "libx265": "hevc",
        "libsvtav1": "av1",
        "libaom-av1": "av1",
        "librav1e": "av1",
        "libvpx": "vp8",
        "libvpx-vp9": "vp9",
        "libmp3lame": "mp3",
        "libopus": "opus",
        "libvorbis": "vorbis",
        "libfdk_aac": "aac",
    }
    if encoder_name in known:
        return known[encoder_name]
    for suffix in HARDWARE_SUFFIXES:
        if encoder_name.endswith(suffix):
            return encoder_name[: -len(suffix)]
    return encoder_name


# ---------------------------------------------------------------------------
# Менеджер
# ---------------------------------------------------------------------------


class CapabilityManager:
    """Собирает и кэширует возможности FFmpeg (раздел 9).

    Кэш инвалидируется при изменении версии или пути к FFmpeg.
    """

    def __init__(self, ffmpeg_service, cache_path: Path | None = None) -> None:
        self.service = ffmpeg_service
        self.cache_path = Path(cache_path) if cache_path else None
        self._capabilities: FFmpegCapabilities | None = None
        self._encoder_options: dict[str, list[EncoderOption]] = {}

    @property
    def capabilities(self) -> FFmpegCapabilities:
        if self._capabilities is None:
            self._capabilities = self.detect()
        return self._capabilities

    def detect(self, force: bool = False) -> FFmpegCapabilities:
        """Основной метод: `CapabilityManager.detect() -> FFmpegCapabilities`."""
        if not self.service.available:
            self._capabilities = FFmpegCapabilities()
            return self._capabilities

        version = self.service.binaries.ffmpeg_version
        path = str(self.service.binaries.ffmpeg)

        if not force:
            cached = self._load_cache(version, path)
            if cached is not None:
                log.info("Capabilities загружены из кэша")
                self._capabilities = cached
                return cached

        log.info("Опрос возможностей FFmpeg")
        caps = FFmpegCapabilities(ffmpeg_version=version, ffmpeg_path=path)
        caps.build_configuration = self.service.query("-buildconf")
        caps.encoders = parse_encoders(self.service.query("-encoders"))
        caps.decoders = parse_decoders(self.service.query("-decoders"))
        caps.codecs = parse_codecs(self.service.query("-codecs"))

        muxers, _ = parse_formats(self.service.query("-muxers"))
        _, demuxers = parse_formats(self.service.query("-demuxers"))
        if not muxers or not demuxers:
            all_mux, all_demux = parse_formats(self.service.query("-formats"))
            muxers = muxers or all_mux
            demuxers = demuxers or all_demux
        caps.muxers = muxers
        caps.demuxers = demuxers

        caps.filters = parse_filters(self.service.query("-filters"))
        caps.pix_fmts = parse_pix_fmts(self.service.query("-pix_fmts"))
        caps.sample_fmts = parse_sample_fmts(self.service.query("-sample_fmts"))
        caps.channel_layouts = parse_layouts(self.service.query("-layouts"))
        caps.hwaccels = parse_simple_list(self.service.query("-hwaccels"))
        caps.bsfs = parse_simple_list(self.service.query("-bsfs"))
        caps.hardware_probe = self._probe_hardware(caps)

        self._capabilities = caps
        self._save_cache(caps)
        return caps

    #: Кодеки, аппаратные энкодеры которых имеет смысл проверять. Пробовать
    #: все подряд дорого, а выбирают на деле из этих.
    PROBE_CODECS = ("h264", "hevc", "av1")

    def _probe_hardware(self, caps: FFmpegCapabilities) -> dict[str, str]:
        """Проверяет, какие аппаратные энкодеры действительно работают.

        В сборках FFmpeg для Windows скомпилированы NVENC, Quick Sync и AMF
        сразу, поэтому список ``-encoders`` ничего не говорит о том, что
        заведётся на конкретной машине. Единственный надёжный ответ —
        попробовать закодировать кадр.

        Проба идёт один раз и кладётся в тот же кэш, что и остальные
        возможности: при смене сборки FFmpeg она повторится.
        """
        wanted = [
            name
            for name, info in caps.encoders.items()
            if info.is_hardware and any(name.startswith(f"{c}_") for c in self.PROBE_CODECS)
        ]
        result: dict[str, str] = {}
        for encoder in sorted(wanted):
            ok, output = self.service.try_encode(encoder)
            result[encoder] = "" if ok else probe_reason(output)
            log.info("Проба %s: %s", encoder, "работает" if ok else result[encoder])
        return result

    def encoder_options(self, encoder: str) -> list[EncoderOption]:
        """Опции конкретного энкодера (раздел 21), с кэшем в памяти."""
        if encoder not in self._encoder_options:
            if not self.service.available:
                return []
            text = self.service.encoder_help(encoder)
            self._encoder_options[encoder] = parse_encoder_options(text)
        return self._encoder_options[encoder]

    def encoder_presets(self, encoder: str) -> list[str]:
        """Раздел 13: список preset зависит от конкретного энкодера.

        Если FFmpeg отдаёт preset как enum (nvenc, qsv) — берём его choices.
        Если preset объявлен строкой (libx264/libx265) — предлагаем
        стандартную шкалу из раздела 13. Если параметра нет — пустой список.
        """
        for option in self.encoder_options(encoder):
            if option.name != "preset":
                continue
            if option.choices:
                return option.choices
            return list(STANDARD_PRESETS)
        return []

    def encoder_supports(self, encoder: str, option_name: str) -> bool:
        return any(opt.name == option_name for opt in self.encoder_options(encoder))

    def hardware_summary(self) -> list[str]:
        """Раздел 35: 'H.264 — NVIDIA NVENC' и т. п."""
        caps = self.capabilities
        summary: list[str] = []
        for encoder in sorted(caps.hardware_encoders(), key=lambda e: e.name):
            codec = (encoder.codec or "").upper()
            summary.append(f"{codec} — {encoder.hardware_vendor} / {encoder.name}")
        return summary

    # -- кэш --------------------------------------------------------------
    def _load_cache(self, version: str, path: str) -> FFmpegCapabilities | None:
        if not self.cache_path or not self.cache_path.is_file():
            return None
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Не удалось прочитать кэш capabilities: %s", exc)
            return None
        if data.get("cache_version") != CACHE_VERSION:
            return None
        if data.get("ffmpeg_version") != version or data.get("ffmpeg_path") != path:
            return None
        try:
            return FFmpegCapabilities.from_dict(data)
        except (TypeError, ValueError) as exc:
            log.warning("Кэш capabilities повреждён: %s", exc)
            return None

    def _save_cache(self, caps: FFmpegCapabilities) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps(caps.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Не удалось сохранить кэш capabilities: %s", exc)
