"""Small Windows last-input helper used to defer background maintenance."""
from __future__ import annotations

import ctypes
import os


class _LastInputInfo(ctypes.Structure):
    _fields_ = (("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint))


def system_idle_seconds() -> float:
    """Return seconds since the most recent keyboard or pointer input."""
    if os.name != "nt":
        return float("inf")
    try:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        current = int(ctypes.windll.kernel32.GetTickCount())
        elapsed_ms = (current - int(info.dwTime)) & 0xFFFFFFFF
        return elapsed_ms / 1000.0
    except Exception:
        return 0.0
