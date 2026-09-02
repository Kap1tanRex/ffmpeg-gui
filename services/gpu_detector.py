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


def _detect_windows() -> list[str]:
    output = _run(
        [
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
        ]
    )
    names = [line.strip() for line in output.splitlines() if line.strip()]
    if names:
        return names
    # Резерв на случай недоступности PowerShell/CIM.
    output = _run(["wmic", "path", "win32_VideoController", "get", "name"])
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return [line for line in lines if line.lower() != "name"]


def _detect_linux() -> list[str]:
    output = _run(["lspci", "-nnk"])
    names: list[str] = []
    for line in output.splitlines():
        if "VGA compatible controller" in line or "3D controller" in line:
            names.append(line.split(":", 2)[-1].strip())
    return names


def _detect_macos() -> list[str]:
    output = _run(["system_profiler", "SPDisplaysDataType"])
    names: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Chipset Model:"):
            names.append(stripped.split(":", 1)[1].strip())
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
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(GpuInfo(name=name, vendor=_vendor_of(name)))
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
    if not gpus:
        return "Видеокарты не обнаружены"
    return ", ".join(f"{gpu.name} ({gpu.vendor})" if gpu.vendor != "UNKNOWN" else gpu.name for gpu in gpus)
