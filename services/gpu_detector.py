"""Определение установленного видеоускорителя (GPU).

Используется для автоматического выбора аппаратного энкодера FFmpeg:
NVIDIA -> NVENC, AMD -> AMF/VAAPI, Intel -> Quick Sync/VAAPI.
Ничего не устанавливает и не обращается к сети — только опрашивает
операционную систему доступными системными средствами.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass

from .ffmpeg_service import creation_flags

log = logging.getLogger(__name__)

_VENDOR_KEYWORDS: dict[str, str] = {
    "nvidia": "NVIDIA",
    "geforce": "NVIDIA",
    "quadro": "NVIDIA",
    "amd": "AMD",
    "radeon": "AMD",
    "ati ": "AMD",
    "intel": "INTEL",
}


@dataclass
class GpuInfo:
    name: str
    vendor: str  # NVIDIA | AMD | INTEL | UNKNOWN
    #: Версия драйвера так, как её называет производитель: у NVIDIA «566.36»,
    #: у остальных — то, что сообщает система. Пустая, если определить не вышло.
    driver: str = ""

    def label(self) -> str:
        """Короткая подпись для строки состояния: производитель и драйвер."""
        vendor = self.vendor if self.vendor != "UNKNOWN" else "GPU"
        return f"{vendor} {self.driver}".strip()


def nvidia_release(windows_version: str) -> str:
    """Версия драйвера NVIDIA из той, что показывает Windows.

    Система сообщает NVIDIA-драйвер в своей нумерации — «32.0.15.6094», —
    а на сайте, в панели управления и в требованиях FFmpeg он называется
    «560.94». Правило перевода: склеить две последние части, взять пять
    последних цифр и поставить точку перед двумя последними.
    """
    parts = windows_version.split(".")
    if len(parts) < 2:
        return ""
    digits = "".join(parts[-2:])
    if len(digits) < 5 or not digits.isdigit():
        return ""
    tail = digits[-5:]
    return f"{tail[:3]}.{tail[3:]}"


def _vendor_of(name: str) -> str:
    lowered = name.lower()
    for keyword, vendor in _VENDOR_KEYWORDS.items():
        if keyword in lowered:
            return vendor
    return "UNKNOWN"


def _run(command: list[str], timeout: float = 6.0) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Команда %s не удалась: %s", command, exc)
        return ""
    return result.stdout or ""


#: Разделитель полей в выводе PowerShell — в именах видеокарт его не бывает.
_FIELD_SEP = "|"


def _detect_windows() -> list[tuple[str, str]]:
    """Пары (имя, версия драйвера в нумерации Windows)."""
    output = _run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_VideoController | "
            f'ForEach-Object {{ "$($_.Name){_FIELD_SEP}$($_.DriverVersion)" }}',
        ]
    )
    found: list[tuple[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, driver = line.partition(_FIELD_SEP)
        if name.strip():
            found.append((name.strip(), driver.strip()))
    if found:
        return found

    # Резерв на случай недоступности PowerShell/CIM: имена без драйвера.
    output = _run(["wmic", "path", "win32_VideoController", "get", "name"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return [(line, "") for line in lines if line.lower() != "name"]


def _nvidia_smi_driver() -> str:
    """Версия драйвера прямо от NVIDIA — точнее любого пересчёта.

    ``nvidia-smi`` ставится вместе с драйвером и сразу отвечает в той
    нумерации, которую спрашивает FFmpeg и показывает панель управления.
    """
    output = _run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], timeout=8.0
    )
    for line in output.splitlines():
        candidate = line.strip()
        if candidate and candidate[0].isdigit():
            return candidate
    return ""


def _detect_linux() -> list[tuple[str, str]]:
    output = _run(["lspci", "-nnk"])
    names: list[tuple[str, str]] = []
    for line in output.splitlines():
        if "VGA compatible controller" in line or "3D controller" in line:
            names.append((line.split(":", 2)[-1].strip(), ""))
    return names


def _detect_macos() -> list[tuple[str, str]]:
    output = _run(["system_profiler", "SPDisplaysDataType"])
    names: list[tuple[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Chipset Model:"):
            names.append((stripped.split(":", 1)[1].strip(), ""))
    return names


def detect_gpus() -> list[GpuInfo]:
    """Раздел 35: список видеоускорителей и их производителей."""
    try:
        if sys.platform == "win32":
            names = _detect_windows()
        elif sys.platform == "darwin":
            names = _detect_macos()
        else:
            names = _detect_linux()
    except Exception as exc:  # noqa: BLE001 - определение GPU не должно ронять приложение
        log.warning("Не удалось определить видеокарту: %s", exc)
        names = []

    seen: set[str] = set()
    result: list[GpuInfo] = []
    # nvidia-smi спрашиваем один раз на все карты и только если NVIDIA есть:
    # на машине без неё это лишний запуск процесса.
    nvidia_driver: str | None = None
    for name, system_driver in names:
        if name in seen:
            continue
        seen.add(name)
        vendor = _vendor_of(name)
        driver = system_driver
        if vendor == "NVIDIA":
            if nvidia_driver is None:
                nvidia_driver = _nvidia_smi_driver()
            driver = nvidia_driver or nvidia_release(system_driver) or system_driver
        result.append(GpuInfo(name=name, vendor=vendor, driver=driver))
    return result


# Раздел 35: приоритет суффиксов аппаратных энкодеров FFmpeg по производителю.
_SUFFIXES_BY_VENDOR: dict[str, tuple[str, ...]] = {
    "NVIDIA": ("nvenc",),
    "AMD": ("amf", "vaapi"),
    "INTEL": ("qsv", "vaapi"),
}


def hardware_suffixes_for_vendor(vendor: str) -> tuple[str, ...]:
    return _SUFFIXES_BY_VENDOR.get(vendor.upper(), ())


def summarize(gpus: list[GpuInfo]) -> str:
    """Строка для настроек и подсказки: карта, производитель и драйвер."""
    if not gpus:
        return "Видеокарты не обнаружены"
    parts: list[str] = []
    for gpu in gpus:
        text = gpu.name
        if gpu.vendor != "UNKNOWN":
            text += f" ({gpu.vendor})"
        if gpu.driver:
            text += f", драйвер {gpu.driver}"
        parts.append(text)
    return ", ".join(parts)


def status_caption(gpus: list[GpuInfo]) -> str:
    """Подпись индикатора в строке состояния.

    Места там мало, поэтому берётся первая распознанная карта: производитель
    и версия драйвера — то, что чаще всего и нужно назвать при разборе
    отказа аппаратного кодирования.
    """
    known = [gpu for gpu in gpus if gpu.vendor != "UNKNOWN"]
    if not known:
        return "GPU"
    return known[0].label()
