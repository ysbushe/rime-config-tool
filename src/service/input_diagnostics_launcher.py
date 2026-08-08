"""Launch the optional input diagnostics companion on demand."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess

from src.utils.logger import get_logger

logger = get_logger(__name__)


def diagnostics_command() -> tuple[str, list[str]]:
    """Return the companion command for source and packaged builds."""
    if getattr(sys, "frozen", False):
        companion = Path(sys.executable).with_name("RimeInputDiagnostics.exe")
        if companion.is_file():
            return str(companion), []
        return sys.executable, ["--input-diagnostics"]
    return sys.executable, ["-m", "src.diagnostics_main"]


def diagnostics_working_directory(program: str) -> str:
    """Return a directory where the selected companion command can import."""
    if getattr(sys, "frozen", False):
        return str(Path(program).resolve().parent)
    return str(Path(__file__).resolve().parents[2])


def diagnostics_process_running(pid: int) -> bool:
    """Return whether the detached companion process still exists."""
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD,
        ]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def launch_input_diagnostics() -> tuple[bool, str, int | None]:
    """Start the companion as a detached process; no monitor lives in the host."""
    program, arguments = diagnostics_command()
    working_directory = diagnostics_working_directory(program)
    started = QProcess.startDetached(program, arguments, working_directory)
    ok = bool(started[0] if isinstance(started, tuple) else started)
    pid = int(started[1]) if isinstance(started, tuple) and len(started) > 1 else None
    if ok:
        logger.info(
            "输入诊断程序已启动：program=%s args=%s cwd=%s pid=%s",
            program, arguments, working_directory, pid,
        )
        return True, "输入诊断程序已启动。关闭其窗口后监控即停止。", pid
    logger.error(
        "输入诊断程序启动失败：program=%s args=%s cwd=%s",
        program, arguments, working_directory,
    )
    return False, f"无法启动输入诊断程序：{program}", None