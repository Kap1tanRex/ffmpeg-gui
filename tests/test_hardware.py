"""Аппаратные энкодеры: проба, отбор в списке и откат на процессор.

Проверки не требуют ни FFmpeg, ни видеокарты: вывод FFmpeg и запуск заданий
подменяются заглушками.
"""

from __future__ import annotations

from pathlib import Path

from ffmpeg_gui.core.capabilities import probe_reason
from ffmpeg_gui.core.error_analyzer import ErrorAnalyzer, ErrorCategory
from ffmpeg_gui.core.models import Job
from ffmpeg_gui.gui.tabs._controls import build_video_codec_choices
from ffmpeg_gui.services.process_manager import RunResult
from ffmpeg_gui.services.queue_manager import QueueCallbacks, QueueManager

#: Настоящий вывод FFmpeg с машины, где стоит RTX 3080, но NVENC не завёлся.
NVENC_FAILURE = """[h264_nvenc @ 0000022cda16e880] No capable devices found
[vost#0:0/h264_nvenc @ 0000022cda0f7e40] [enc:h264_nvenc @ 0000022cda10c280] Error while opening encoder - maybe incorrect parameters such as bit_rate, rate, width or height.
[vf#0:0 @ 0000022cda0f80c0] Error sending frames to consumers: Generic error in an external library
[vf#0:0 @ 0000022cda0f80c0] Task finished with error: Generic error in an external library
[vost#0:0/h264_nvenc @ 0000022cda0f7e40] Could not open encoder before EOF
[vost#0:0/h264_nvenc @ 0000022cda0f7e40] Task finished with error: Invalid argument
[out#0/mp4 @ 0000022cda10fc40] Nothing was written into output file, because at least one of its streams received no packets."""


# --- разбор причины отказа --------------------------------------------------
def test_reason_prefers_specific_cause_over_generic():
    """Правило про фильтры проверяется раньше — оно не должно перехватывать."""
    error = ErrorAnalyzer().analyze(NVENC_FAILURE, 3752568763)
    assert error.category is ErrorCategory.HWACCEL_ERROR


def test_probe_reason_extracts_driver_message():
    output = "[h264_nvenc @ 0x1] The minimum required Nvidia driver for nvenc is 570.0 or newer"
    assert probe_reason(output) == "The minimum required Nvidia driver for nvenc is 570.0 or newer"


def test_probe_reason_strips_widget_prefix():
    assert probe_reason("[AMF @ 0x1] DLL amfrt64.dll failed to open") == (
        "DLL amfrt64.dll failed to open"
    )


def test_probe_reason_ignores_decode_error_counter():
    """«0 decode errors» — строка отчёта, а не сообщение об ошибке."""
    output = (
        "[in#0 @ 0x1] Input stream #0:0 (video): 6 packets read; 0 decode errors;\n"
        "Conversion failed!"
    )
    assert probe_reason(output) == "не поддерживается на этой машине"


def test_probe_reason_on_empty_output():
    assert probe_reason("") == "энкодер не запустился"


# --- отбор энкодеров в списке кодеков ---------------------------------------
class _Encoder:
    def __init__(self, name: str, hardware: bool, vendor: str = "") -> None:
        self.name = name
        self.is_hardware = hardware
        self.hardware_vendor = vendor


class _Codec:
    def __init__(self, name: str) -> None:
        self.name = name


class _Caps:
    def __init__(self, probe: dict[str, str] | None = None) -> None:
        self.hardware_probe = probe or {}

    def encoding_codecs(self, media_type):
        return [_Codec("h264")]

    def encoders_for_codec(self, codec):
        return [
            _Encoder("libx264", False),
            _Encoder("h264_nvenc", True, "NVIDIA NVENC"),
            _Encoder("h264_qsv", True, "Intel Quick Sync"),
        ]


def _offered(caps, **kwargs) -> set[str]:
    choices = build_video_codec_choices(caps, show_all=False, **kwargs)
    return {encoder for _codec, encoder in choices.values() if encoder}


def test_broken_hardware_encoder_is_hidden_even_with_matching_gpu():
    """Видеокарта NVIDIA есть, но NVENC не завёлся — предлагать его нельзя."""
    caps = _Caps({"h264_nvenc": "No capable devices found", "h264_qsv": "нет"})
    offered = _offered(caps, hw_suffixes=("nvenc",))
    assert "h264_nvenc" not in offered
    assert "libx264" in offered


def test_working_hardware_encoder_is_offered():
    caps = _Caps({"h264_nvenc": "", "h264_qsv": "нет"})
    offered = _offered(caps)
    assert "h264_nvenc" in offered
    assert "h264_qsv" not in offered


def test_without_probe_falls_back_to_vendor_filter():
    """Старый кэш без пробы не должен прятать всё подряд."""
    offered = _offered(_Caps(), hw_suffixes=("nvenc",))
    assert "h264_nvenc" in offered
    assert "h264_qsv" not in offered


# --- откат задания на процессор ---------------------------------------------
class _Builder:
    def build(self, job, output_override=None, for_execution=False,
              pass_number=None, passlog=None):
        return ["ffmpeg", "-c:v", job.video_encoder or job.video.encoder or "libx264"]


class _Validator:
    class _Result:
        ok = True
        errors: list = []

    def validate(self, job):
        return self._Result()


class _Filesystem:
    def temp_output_for(self, path):
        return Path(str(path) + ".tmp")

    def unique_path(self, path):
        return path


class _Processes:
    """Падает на любом энкодере NVENC, на остальных отрабатывает."""

    def __init__(self, category=ErrorCategory.HWACCEL_ERROR) -> None:
        self.commands: list[list[str]] = []
        self.category = category

    def run_job(self, job, command, duration, on_progress=None, on_log=None,
                final_output=None, finalize=True):
        self.commands.append(command)
        if "nvenc" in command[-1]:
            analyzer = ErrorAnalyzer()
            error = analyzer.analyze(NVENC_FAILURE, 1)
            error.category = self.category
            return RunResult(returncode=1, error=error, stderr=NVENC_FAILURE)
        return RunResult(returncode=0, output_path=final_output)

    def cancel_all(self):
        pass


def _run(job, category=ErrorCategory.HWACCEL_ERROR):
    processes = _Processes(category)
    logs: list[str] = []
    queue = QueueManager(
        processes,
        _Builder(),
        _Validator(),
        _Filesystem(),
        QueueCallbacks(on_job_log=lambda _job, line: logs.append(line)),
    )
    queue._run_job(job)
    return processes.commands, logs


def _job(**fields) -> Job:
    job = Job(input_files=[r"C:\in.mp4"], output_file=r"C:\out.mp4")
    for key, value in fields.items():
        setattr(job, key, value)
    return job


def test_retries_on_cpu_when_encoder_chosen_by_hand():
    job = _job()
    job.video.encoder = "h264_nvenc"
    commands, logs = _run(job)
    assert [c[-1] for c in commands] == ["h264_nvenc", "libx264"]
    assert job.status == "COMPLETED"
    assert any("процессоре" in line for line in logs)


def test_retries_on_cpu_when_encoder_chosen_automatically():
    job = _job(video_encoder="h264_nvenc")
    commands, _logs = _run(job)
    assert [c[-1] for c in commands] == ["h264_nvenc", "libx264"]
    assert job.status == "COMPLETED"


def test_second_failure_does_not_loop():
    """Повтор ровно один: иначе задание крутилось бы бесконечно."""
    job = _job(video_encoder="h264_nvenc")

    class AlwaysFails(_Processes):
        def run_job(self, job, command, duration, **kwargs):
            self.commands.append(command)
            error = ErrorAnalyzer().analyze(NVENC_FAILURE, 1)
            return RunResult(returncode=1, error=error, stderr=NVENC_FAILURE)

    processes = AlwaysFails()
    QueueManager(processes, _Builder(), _Validator(), _Filesystem())._run_job(job)
    assert len(processes.commands) == 2
    assert job.status == "FAILED"


def test_other_errors_do_not_trigger_retry():
    job = _job(video_encoder="h264_nvenc")
    commands, _logs = _run(job, category=ErrorCategory.NO_SPACE)
    assert len(commands) == 1
