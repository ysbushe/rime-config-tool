"""Windows single-instance guard and wake-up signal for the desktop application."""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import time


_INSTANCE_NAMESPACE_ENV = "RIME_CONFIG_INSTANCE_NAMESPACE"


def _kernel_object_names() -> tuple[str, str]:
    """Return the default kernel names or an explicitly isolated test pair."""
    namespace = os.environ.get(_INSTANCE_NAMESPACE_ENV, "").strip()
    if not namespace:
        return (
            SingleInstanceGuard._NAME,
            SingleInstanceGuard._WAKE_EVENT,
        )
    suffix = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    prefix = rf"Local\RimeConfigTool.Test.{suffix}"
    return f"{prefix}.SingleInstance", f"{prefix}.ShowMainWindow"


class SingleInstanceGuard:
    """Hold the primary-instance mutex and accept requests to restore its window."""

    _NAME = r"Local\RimeConfigTool.SingleInstance"
    _WAKE_EVENT = r"Local\RimeConfigTool.ShowMainWindow"
    _ALREADY_EXISTS = 183
    _EVENT_MODIFY_STATE = 0x0002
    _WAIT_OBJECT_0 = 0

    def __init__(self) -> None:
        self._name, self._wake_event = _kernel_object_names()
        self._handle = None
        self._wake_handle = None

    def acquire(self) -> bool:
        if not sys.platform.startswith("win"):
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, self._name)
            if not handle:
                return True  # Do not prevent startup when the OS call itself fails.
            if int(kernel32.GetLastError()) == self._ALREADY_EXISTS:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
            self._handle = handle
            kernel32.CreateEventW.restype = ctypes.c_void_p
            self._wake_handle = kernel32.CreateEventW(
                None, False, False, self._wake_event
            )
            return True
        except Exception:
            return True

    def request_existing_instance(self, timeout_seconds: float = 1.0) -> bool:
        """Ask the already-running process to restore its main window."""
        if not sys.platform.startswith("win"):
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            deadline = time.monotonic() + max(0.0, timeout_seconds)
            while True:
                handle = kernel32.OpenEventW(
                    self._EVENT_MODIFY_STATE, False, self._wake_event
                )
                if handle:
                    try:
                        return bool(kernel32.SetEvent(handle))
                    finally:
                        kernel32.CloseHandle(ctypes.c_void_p(handle))
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)
        except Exception:
            return False

    def consume_wakeup(self) -> bool:
        """Return whether another launch asked this instance to show its window."""
        if self._wake_handle is None or not sys.platform.startswith("win"):
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(
                ctypes.c_void_p(self._wake_handle), 0
            ) == self._WAIT_OBJECT_0
        except Exception:
            return False

    def release(self) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            kernel32 = ctypes.windll.kernel32
            if self._wake_handle is not None:
                kernel32.CloseHandle(ctypes.c_void_p(self._wake_handle))
        except Exception:
            pass
        finally:
            self._wake_handle = None
        try:
            if self._handle is not None:
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        except Exception:
            pass
        self._handle = None
