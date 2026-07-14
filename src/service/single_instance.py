"""Windows single-instance guard for the desktop application."""
from __future__ import annotations

import ctypes
import sys


class SingleInstanceGuard:
    """Hold a named Windows mutex for the lifetime of one app instance."""

    _NAME = r"Local\RimeConfigTool.SingleInstance"
    _ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        if not sys.platform.startswith("win"):
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, self._NAME)
            if not handle:
                return True  # Do not prevent startup when the OS call itself fails.
            if int(kernel32.GetLastError()) == self._ALREADY_EXISTS:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
            self._handle = handle
            return True
        except Exception:
            return True

    def release(self) -> None:
        if self._handle is None or not sys.platform.startswith("win"):
            return
        try:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        except Exception:
            pass
        self._handle = None
