"""Сборка команды FFmpeg из модели Job.

Разделы ТЗ: 22 (Command Builder), 15 (stream copy), 17 (быстрая/точная обрезка),
26 (-progress pipe:1), 36-41 (видео, аудио, субтитры, mapping, фильтры, HDR),
48 (показ команды), 49 (экспертный режим через структурированную модель).

Модуль ничего не знает о GUI и не выполняет процессы: он возвращает список
аргументов, пригодный для `subprocess.Popen(args, shell=False)`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .capabilities import FFmpegCapabilities
from .compatibility import CompatibilityService
from .models import Job, MediaFile, StreamType, SubtitleMode, parse_bitrate


@dataclass
class EncodeSettings:
    """Компактное представление настроек кодирования (раздел 22)."""

    video_encoder: str
    audio_encoder: str | None = None
    crf: float | None = None
    bitrate: str | None = None
    preset: str | None = None
    width: int | None = None
    height: int | None = None


class CommandBuildError(ValueError):
    """Команду невозможно собрать из текущего состояния Job."""


class CommandBuilder:
    """Преобразует Job в список аргументов FFmpeg."""

    def __init__(
        self,
        capabilities: FFmpegCapabilities | None = None,
        ffmpeg_binary: str = "ffmpeg",
    ) -> None:
        self.caps = capabilities or FFmpegCapabilities()
        self.compat = CompatibilityService(self.caps)
        self.ffmpeg_binary = ffmpeg_binary
        self._option_cache: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    def build(
        self,
        job: Job,
        output_override: str | Path | None = None,
        for_execution: bool = False,
        binary: str | None = None,
        pass_number: int | None = None,
        passlog: str | Path | None = None,
    ) -> list[str]:
        """Полная команда: [ffmpeg, -i, вход, ..., выход].

        `for_execution=True` добавляет служебные ключи прогресса (раздел 26).
        `output_override` используется для записи во временный файл (раздел 30).
        `pass_number` — номер прохода при двухпроходном кодировании: первый
        проход только собирает статистику в `passlog` и пишет в никуда.
        """
        if not job.input_files:
            raise CommandBuildError("Не задан входной файл")

        output = Path(output_override) if output_override else job.output_path
        if not str(output):
            raise CommandBuildError("Не задан выходной файл")

        args: list[str] = [binary or self.ffmpeg_binary]
        args += self._global_options(for_execution)

        # Склейка без перекодирования — совсем другая команда: один вход,
        # список файлов, и никаких энкодеров.
        if job.concat and job.concat_list:
            return args + [
                "-f", "concat",
                "-safe", "0",
                "-i", str(job.concat_list),
                "-c", "copy",
                str(output),
            ]

        args += self._input_section(job)
        args += self._map_section(job)
        args += self._video_section(job)
        if pass_number:
            args += ["-pass", str(pass_number), "-passlogfile", str(passlog)]
        if pass_number == 1:
            # Первому проходу нужна только статистика видео: звук и субтитры
            # он бы кодировал впустую, а результат уходит в никуда.
            # Явное отображение потоков (раздел 39) при этом ломать нельзя —
            # там мог быть выбран конкретный звук, и -an даст конфликт.
            if not job.stream_map:
                args += ["-an", "-sn"]
            args += ["-f", "null", os.devnull]
            return args
        args += self._audio_section(job)
        args += self._subtitle_section(job)
        args += self._trim_output_section(job)
        args += self._metadata_section(job)
        args += self._container_section(job, output)
        args += [str(arg) for arg in job.output_options]
        args.append(str(output))
        return args

    def build_preview(self, job: Job) -> list[str]:
        """Команда для окна «Показать команду FFmpeg» (раздел 48)."""
        return self.build(job, for_execution=False)

    # ------------------------------------------------------------------
    @staticmethod
    def _global_options(for_execution: bool) -> list[str]:
        options = ["-hide_banner", "-nostdin", "-y"]
        if for_execution:
            # Раздел 26: машиночитаемый прогресс в stdout.
            options += ["-loglevel", "error", "-nostats", "-progress", "pipe:1"]
        return options

    def _input_section(self, job: Job) -> list[str]:
        args: list[str] = []

        if job.hwaccel:
            args += ["-hwaccel", job.hwaccel]

        args += [str(arg) for arg in job.input_options]

        trim = job.trim
        # Раздел 17: при быстрой обрезке -ss ставится ДО -i (input seek),
        # при точной — тоже до входа, но с последующим перекодированием;
        # FFmpeg выполняет точный поиск и декодирует до нужного кадра.
        if trim.enabled and trim.start:
            args += ["-ss", f"{trim.start:.3f}"]
            if trim.accurate:
                args += ["-accurate_seek"]

        for input_file in job.input_files:
            args += ["-i", str(input_file)]

        # Водяной знак — второй вход; порядок важен, на него ссылается [1:v].
        if job.overlay_path:
            args += ["-i", str(job.overlay_path)]

        if job.subtitles.mode is SubtitleMode.BURN and job.subtitles.burn_source:
            # Файл субтитров подключается фильтром, а не отдельным входом.
            pass
        return args

    @staticmethod
    def _map_section(job: Job) -> list[str]:
        """Раздел 39. Явный выбор потоков."""
        args: list[str] = []
        for mapping in job.stream_map:
            args += ["-map", mapping]
        return args

    # -- целевой размер --------------------------------------------------
    #: Запас на заголовки контейнера и промах кодировщика по битрейту.
    SIZE_MARGIN = 0.97

    def target_bitrate(self, job: Job) -> int | None:
        """Битрейт видео (бит/с), при котором файл уложится в заданный размер.

        Размер задаётся в мебибайтах — так же, как его показывает проводник,
        иначе «100 МБ» в программе и в свойствах файла означали бы разное.
        Звук вычитается: его биты видео забрать не может.
        """
        size_mb = job.video.target_size_mb
        duration = job.effective_duration()
        if not size_mb or not duration or duration <= 0:
            return None
        total_bps = size_mb * 1024 * 1024 * 8 / duration
        video_bps = total_bps * self.SIZE_MARGIN - self.audio_bps(job)
        # Ниже этого порога получается не видео, а мозаика; пусть лучше файл
        # выйдет больше заказанного, чем непригодным.
        return max(int(video_bps), 32_000)

    @staticmethod
    def audio_bps(job: Job) -> float:
        audio = job.audio
        if audio.mode == "none":
            return 0.0
        if audio.mode == "copy":
            streams = job.source.audio_streams if job.source else []
            known = [s.bit_rate for s in streams if s.bit_rate]
            return float(sum(known)) if known else 128_000.0
        return parse_bitrate(audio.bitrate) or 128_000.0

    # -- видео ----------------------------------------------------------
    def _video_section(self, job: Job) -> list[str]:
        video = job.video
        if video.mode == "none":
            return ["-vn"]
        if video.mode == "copy":
            return ["-c:v", "copy"]

        encoder = job.video_encoder or video.encoder or self.compat.resolve_encoder(
            video.codec, "video"
        )
        if not encoder:
            raise CommandBuildError(
                f"Не найден энкодер для видеокодека «{video.codec}»"
            )

        args: list[str] = ["-c:v", encoder]

        # Качество (раздел 12).
        if video.quality_mode == "crf" and video.crf is not None:
            args += self._quality_args(encoder, video.crf)
        elif video.quality_mode == "bitrate" and video.bitrate:
            args += ["-b:v", video.bitrate]
        elif video.quality_mode == "size":
            bitrate = self.target_bitrate(job)
            if bitrate is None:
                raise CommandBuildError(
                    "Для режима «Целевой размер» нужны размер и длительность "
                    "исходника — дождитесь анализа файла"
                )
            args += ["-b:v", str(bitrate)]
        elif video.quality_mode == "lossless":
            args += self._quality_args(encoder, 0)
        elif video.quality_mode == "qp" and video.crf is not None:
            args += ["-qp", str(int(video.crf))]

        # Preset (раздел 13) — только если энкодер его поддерживает.
        if video.preset and self._encoder_has_option(encoder, "preset"):
            args += ["-preset", video.preset]
        if video.tune and self._encoder_has_option(encoder, "tune"):
            args += ["-tune", video.tune]
        if video.profile:
            args += ["-profile:v", video.profile]
        if video.level:
            args += ["-level", str(video.level)]
        if video.gop:
            args += ["-g", str(video.gop)]
        if video.bframes is not None:
            args += ["-bf", str(video.bframes)]
        if video.pix_fmt:
            args += ["-pix_fmt", video.pix_fmt]

        for key, value in video.extra_options.items():
            args += [f"-{key.lstrip('-')}", str(value)]

        args += self._filter_args(job)

        # Сохранение цветовых характеристик HDR (раздел 41).
        args += self._color_args(job)

        for stream, bsf in job.bitstream_filters.items():
            args += [f"-bsf:{stream}", bsf]

        return args

    def _quality_args(self, encoder: str, value: float) -> list[str]:
        """Выбирает подходящий ключ качества для конкретного энкодера.

        Для аппаратных энкодеров одного параметра качества недостаточно:
        без явного снятия ограничения по битрейту (или явного CQP-режима)
        драйвер всё равно целится в собственный битрейт по умолчанию,
        из-за чего результат может оказаться в разы больше исходника при
        том же значении «качества» — это раздутие, а не настоящий CRF.
        """
        text = f"{value:g}"
        if self._encoder_has_option(encoder, "crf"):
            return ["-crf", text]
        if self._encoder_has_option(encoder, "cq"):
            args = ["-cq", text]
            if encoder.endswith("_nvenc"):
                # Без -b:v 0 nvenc всё равно целится в свой битрейт по
                # умолчанию поверх -cq — снимаем ограничение явно.
                args += ["-b:v", "0"]
            return args
        if encoder.endswith("_amf"):
            # AMF не регистрирует общий "qp" — только qp_i/qp_p/qp_b, и без
            # -rc cqp продолжает работать в режиме постоянного битрейта.
            return ["-rc", "cqp", "-qp_i", text, "-qp_p", text, "-qp_b", text]
        if self._encoder_has_option(encoder, "qp"):
            return ["-qp", text]
        if self._encoder_has_option(encoder, "global_quality"):
            return ["-global_quality", text]
        if self.compat.supports_crf(encoder):
            return ["-crf", text]
        return ["-q:v", text]

    #: Положение водяного знака -> выражение overlay=x:y. ``M`` — отступ.
    OVERLAY_POSITIONS: dict[str, str] = {
        "tl": "{m}:{m}",
        "tr": "W-w-{m}:{m}",
        "bl": "{m}:H-h-{m}",
        "br": "W-w-{m}:H-h-{m}",
        "center": "(W-w)/2:(H-h)/2",
    }

    #: К чему приводятся куски перед склейкой, если у них разный звук.
    CONCAT_RATE = 48000

    def _concat_filter_args(self, job: Job, filters: list[str]) -> list[str]:
        """Граф склейки с перекодированием.

        Фильтр ``concat`` отказывается работать, если у кусков не совпадают
        размер кадра, соотношение пикселей или параметры звука, — а куски
        как раз потому и перекодируются, что они разные. Поэтому каждый
        вход сначала приводится к общему виду: вписывается в кадр первого
        файла с чёрными полями (обрезать чужой кадр никто не просил), а звук
        сводится к одной частоте и раскладке.
        """
        count = len(job.input_files)
        audio = 1 if job.concat_audio else 0
        stream = None
        if job.source:
            stream = next((s for s in job.source.streams if s.type is StreamType.VIDEO), None)
        width = job.video.width or (stream.width if stream else None)
        height = job.video.height or (stream.height if stream else None)

        parts: list[str] = []
        # Дорожки чередуются: видео, звук, видео, звук — так их ждёт concat.
        pads: list[str] = []
        for index in range(count):
            if width and height:
                parts.append(
                    f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]"
                )
                pads.append(f"[v{index}]")
            else:
                pads.append(f"[{index}:v]")
            if audio:
                parts.append(
                    f"[{index}:a]aresample={self.CONCAT_RATE},"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
                )
                pads.append(f"[a{index}]")

        head = "".join(pads)
        parts.append(f"{head}concat=n={count}:v=1:a={audio}[cv]" + ("[ca]" if audio else ""))
        video_label = "[cv]"
        if filters:
            parts.append(f"[cv]{','.join(filters)}[v]")
            video_label = "[v]"

        maps = ["-map", video_label]
        if audio:
            maps += ["-map", "[ca]"]
        return ["-filter_complex", ";".join(parts), *maps]

    def _filter_args(self, job: Job) -> list[str]:
        """``-vf`` для обычного случая и ``-filter_complex`` для наложения.

        Картинка поверх видео — второй вход, а простой ``-vf`` работает с
        одним. Как только появляется наложение, потоки приходится выбирать
        руками: после ``-map [v]`` FFmpeg перестаёт подбирать их сам.
        """
        filters = self.build_video_filters(job)

        # Склейка с перекодированием: несколько входов сводятся в один поток
        # фильтром concat, дальше всё как обычно.
        if job.concat and len(job.input_files) > 1:
            return self._concat_filter_args(job, filters)

        gif = (job.container or "").lower() == "gif"
        if not job.overlay_path and not gif:
            return ["-vf", ",".join(filters)] if filters else []

        parts = [f"[0:v]{','.join(filters) if filters else 'null'}[base]"]
        last = "[base]"

        if job.overlay_path:
            margin = max(0, int(job.overlay_margin))
            position = self.OVERLAY_POSITIONS.get(
                job.overlay_position, self.OVERLAY_POSITIONS["br"]
            ).format(m=margin)
            mark = "[1:v]"
            if job.overlay_opacity < 1.0:
                parts.append(
                    f"[1:v]format=rgba,colorchannelmixer=aa={job.overlay_opacity:.2f}[mark]"
                )
                mark = "[mark]"
            # Имя последнего узла зависит от того, будет ли дальше палитра.
            last = "[marked]" if gif else "[v]"
            parts.append(f"[base]{mark}overlay={position}{last}")

        if gif:
            # У GIF всего 256 цветов на кадр. Палитра, посчитанная по самому
            # ролику, отличает приличную анимацию от грязной ряби.
            parts.append(f"{last}split[a][b]")
            parts.append("[a]palettegen=stats_mode=diff[p]")
            parts.append("[b][p]paletteuse=dither=bayer:bayer_scale=3[v]")
            return ["-filter_complex", ";".join(parts), "-map", "[v]", "-an", "-sn"]

        # Сюда попадаем только с наложением: без него и без GIF вышли раньше.
        return [
            "-filter_complex",
            ";".join(parts),
            "-map", "[v]",
            "-map", "0:a?",
            "-map", "0:s?",
        ]

    def build_video_filters(self, job: Job) -> list[str]:
        """Фильтры видео (раздел 40): пользовательские + fps + scale + субтитры."""
        video = job.video
        filters: list[str] = list(job.filters)

        if video.fps:
            filters.append(f"fps={video.fps:g}")

        scale = self._scale_filter(video)
        if scale:
            filters.append(scale)

        if job.subtitles.mode is SubtitleMode.BURN:
            source = job.subtitles.burn_source or (
                job.input_files[0] if job.input_files else ""
            )
            if source:
                path = escape_filter_path(source)
                filters.append(f"subtitles='{path}':si={job.subtitles.burn_stream_index}")

        return filters

    @staticmethod
    def _scale_filter(video) -> str | None:
        """Раздел 36: масштабирование с учётом aspect ratio и запрета апскейла."""
        width, height = video.width, video.height
        if not width and not height:
            return None

        divisor = ":force_divisible_by=2" if video.round_dimensions else ""

        if width and height:
            if video.keep_aspect:
                if video.no_upscale:
                    return (
                        f"scale=w='min({width},iw)':h='min({height},ih)'"
                        f":force_original_aspect_ratio=decrease{divisor}"
                    )
                return f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease{divisor}"
            return f"scale={width}:{height}"

        other = -2 if video.round_dimensions else -1
        if width:
            expression = f"'min({width},iw)'" if video.no_upscale else str(width)
            return f"scale={expression}:{other}"
        expression = f"'min({height},ih)'" if video.no_upscale else str(height)
        return f"scale={other}:{expression}"

    def _color_args(self, job: Job) -> list[str]:
        """Переносит color_* из источника, если это HDR (раздел 41)."""
        source: MediaFile | None = job.source
        if source is None or not source.has_hdr:
            return []
        stream = source.primary_video
        if stream is None:
            return []
        args: list[str] = []
        if stream.color_primaries:
            args += ["-color_primaries", stream.color_primaries]
        if stream.color_transfer:
            args += ["-color_trc", stream.color_transfer]
        if stream.color_space:
            args += ["-colorspace", stream.color_space]
        if stream.color_range:
            args += ["-color_range", stream.color_range]
        return args

    # -- аудио ----------------------------------------------------------
    def _audio_section(self, job: Job) -> list[str]:
        audio = job.audio
        if audio.mode == "none":
            return ["-an"]
        if audio.mode == "copy":
            return ["-c:a", "copy"]

        encoder = job.audio_encoder or audio.encoder or self.compat.resolve_encoder(
            audio.codec, "audio"
        )
        if not encoder:
            raise CommandBuildError(
                f"Не найден энкодер для аудиокодека «{audio.codec}»"
            )

        args: list[str] = ["-c:a", encoder]
        codec = self.compat.codec_of_encoder(encoder)
        # Для lossless-кодеков битрейт бессмысленен.
        if audio.bitrate and codec not in ("flac", "alac") and not codec.startswith("pcm"):
            args += ["-b:a", audio.bitrate]
        if audio.sample_rate:
            args += ["-ar", str(audio.sample_rate)]
        if audio.channels:
            args += ["-ac", str(audio.channels)]
        if audio.channel_layout:
            args += ["-channel_layout", audio.channel_layout]

        for key, value in audio.extra_options.items():
            args += [f"-{key.lstrip('-')}", str(value)]

        filters = list(job.audio_filters)
        if audio.volume is not None and abs(audio.volume - 1.0) > 1e-6:
            filters.append(f"volume={audio.volume:g}")
        if audio.normalize:
            # Вещательная норма EBU R128: -16 LUFS — то, к чему приводят звук
            # видеоплощадки. Ставится последним, чтобы ручная громкость выше
            # не сдвигала результат замера.
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if filters:
            args += ["-af", ",".join(filters)]
        return args

    # -- субтитры -------------------------------------------------------
    def _subtitle_section(self, job: Job) -> list[str]:
        mode = job.subtitles.mode
        if mode is SubtitleMode.REMOVE or mode is SubtitleMode.BURN:
            # При burn-in субтитры уже вшиты в картинку.
            return ["-sn"]
        if mode is SubtitleMode.COPY:
            return ["-c:s", "copy"]
        if mode is SubtitleMode.CONVERT:
            muxer = job.container or "matroska"
            encoder = job.subtitles.encoder or self.compat.default_subtitle_codec(muxer)
            if encoder:
                return ["-c:s", encoder]
            return ["-c:s", "copy"]
        return []

    # -- обрезка --------------------------------------------------------
    @staticmethod
    def _trim_output_section(job: Job) -> list[str]:
        """Раздел 16: конец задаётся через -to/-t относительно точки -ss."""
        trim = job.trim
        if not trim.enabled:
            return []
        if trim.duration is not None:
            return ["-t", f"{trim.duration:.3f}"]
        if trim.end is not None:
            start = trim.start or 0.0
            length = max(0.0, trim.end - start)
            return ["-t", f"{length:.3f}"]
        return []

    # -- метаданные и контейнер -----------------------------------------
    @staticmethod
    def _metadata_section(job: Job) -> list[str]:
        args: list[str] = []
        if job.metadata_mode == "strip":
            args += ["-map_metadata", "-1"]
        else:
            args += ["-map_metadata", "0"]
        for key, value in job.custom_metadata.items():
            args += ["-metadata", f"{key}={value}"]
        return args

    def _container_section(self, job: Job, output: Path) -> list[str]:
        args: list[str] = []
        container = job.container
        if container:
            inferred = self.compat.muxer_for_extension(output.suffix)
            if inferred != container:
                args += ["-f", container]
            if container in ("mp4", "mov", "ipod"):
                args += ["-movflags", "+faststart"]
        return args

    # -- вспомогательное -------------------------------------------------
    def _encoder_has_option(self, encoder: str, option: str) -> bool:
        """Проверка по данным capabilities; при их отсутствии — эвристика."""
        info = self.caps.encoders.get(encoder)
        if info is None:
            return option in {"preset", "crf"} and encoder.startswith("libx")
        known = self._option_cache.get(encoder)
        if known is None:
            known = _static_encoder_options(encoder)
            self._option_cache[encoder] = known
        return option in known

    def set_encoder_options(self, encoder: str, options: set[str]) -> None:
        """Позволяет CapabilityManager сообщить реальный список опций."""
        self._option_cache[encoder] = options


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _static_encoder_options(encoder: str) -> set[str]:
    """Резервное знание об опциях, если `-h encoder=` недоступен."""
    options: set[str] = set()
    if encoder.startswith(("libx264", "libx265", "libsvtav1", "libaom", "librav1e", "libvpx")):
        options |= {"crf", "preset", "tune"}
    if encoder.endswith("_nvenc"):
        options |= {"preset", "cq", "tune"}
    if encoder.endswith("_qsv"):
        options |= {"preset", "global_quality"}
    if encoder.endswith("_amf"):
        options |= {"quality", "qp_i"}
    if encoder.endswith("_vaapi"):
        options |= {"qp"}
    return options


def escape_filter_path(path: str | Path) -> str:
    """Экранирование пути внутри filtergraph (Windows-пути с ':' и '\\')."""
    text = str(path).replace("\\", "/")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    return text


def quote_argument(argument: str) -> str:
    """Кавычки для отображения команды пользователю (раздел 48)."""
    text = str(argument)
    if not text:
        return '""'
    if any(character in text for character in ' \t"\'&|<>^()'):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def command_to_string(command: list[str]) -> str:
    """Однострочное представление команды для копирования."""
    return " ".join(quote_argument(part) for part in command)
