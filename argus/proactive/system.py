"""Read-only battery and disk health checks using the Python standard library."""

from __future__ import annotations

import ctypes
from pathlib import Path
import shutil
import sys

from argus.proactive.interfaces import SystemSnapshot


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ac_line_status", ctypes.c_ubyte),
        ("battery_flag", ctypes.c_ubyte),
        ("battery_life_percent", ctypes.c_ubyte),
        ("system_status_flag", ctypes.c_ubyte),
        ("battery_life_time", ctypes.c_ulong),
        ("battery_full_life_time", ctypes.c_ulong),
    ]


class LocalSystemReader:
    def __init__(self, disk_path: Path) -> None:
        self._disk_path = Path(disk_path).resolve()

    @staticmethod
    def _battery() -> tuple[int | None, bool | None]:
        if sys.platform != "win32":
            return None, None
        status = _SystemPowerStatus()
        try:
            succeeded = ctypes.windll.kernel32.GetSystemPowerStatus(  # type: ignore[attr-defined]
                ctypes.byref(status)
            )
        except (AttributeError, OSError):
            return None, None
        if not succeeded or status.battery_flag == 128:
            return None, None
        percent = (
            int(status.battery_life_percent)
            if status.battery_life_percent <= 100
            else None
        )
        plugged_in = (
            True
            if status.ac_line_status == 1
            else False
            if status.ac_line_status == 0
            else None
        )
        return percent, plugged_in

    def snapshot(self) -> SystemSnapshot:
        battery_percent, plugged_in = self._battery()
        try:
            usage = shutil.disk_usage(self._disk_path)
            disk_free_percent = (
                100.0 * usage.free / usage.total if usage.total else None
            )
        except OSError:
            disk_free_percent = None
        return SystemSnapshot(
            battery_percent=battery_percent,
            plugged_in=plugged_in,
            disk_free_percent=disk_free_percent,
        )
