"""Privacy-safe Windows input-method diagnostics.

The plug-in observes process/window metadata and modifier-key state only. It never
reads window titles, keystroke text, the clipboard, or an IME composition buffer.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from src.config.paths import input_diagnostics_log_path

_MONITORED_PROCESSES = {
    "weaselserver.exe", "weaseldeployer.exe", "ctfmon.exe",
    "textinputhost.exe", "rimeconfig.exe",
}
_WINDOW_CLASS_MARKERS = ("candidate", "ime", "weasel", "popup", "tooltip", "shadow")
_WS_EX_TOOLWINDOW = 0x00000080
_GW_OWNER = 4
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_NAME_CACHE: dict[int, str] = {}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _process_name(pid: int) -> str:
    if os.name != "nt" or not pid:
        return ""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _process_names() -> dict[int, str]:
    """Return PID names, querying the OS only for PIDs not seen last poll."""
    if os.name != "nt":
        return {}
    psapi = ctypes.windll.psapi
    ids = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    if not psapi.EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
        return {}
    count = needed.value // ctypes.sizeof(ctypes.c_ulong)
    live = {int(pid) for pid in ids[:count] if pid}
    for stale_pid in set(_PROCESS_NAME_CACHE) - live:
        _PROCESS_NAME_CACHE.pop(stale_pid, None)
    for pid in live - set(_PROCESS_NAME_CACHE):
        _PROCESS_NAME_CACHE[pid] = _process_name(pid)
    return {pid: _PROCESS_NAME_CACHE.get(pid, "") for pid in live}


def _running_processes(names: dict[int, str]) -> list[dict[str, Any]]:
    result = [
        {"pid": pid, "process": name}
        for pid, name in names.items() if name in _MONITORED_PROCESSES
    ]
    return sorted(result, key=lambda item: (item["process"], item["pid"]))


def _window_record(hwnd: int, foreground: int,
                   process_names: dict[int, str]) -> dict[str, Any] | None:
    user32 = ctypes.windll.user32
    user32.GetWindow.restype = ctypes.c_void_p
    if not user32.IsWindowVisible(hwnd):
        return None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, len(class_buf))
    class_name = class_buf.value
    process = process_names.get(int(pid.value), "")
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    get_long.restype = ctypes.c_ssize_t
    exstyle = int(get_long(hwnd, -20))
    interesting = (
        process in _MONITORED_PROCESSES
        or class_name == "#32768"
        or any(marker in class_name.lower() for marker in _WINDOW_CLASS_MARKERS)
        or bool(exstyle & _WS_EX_TOOLWINDOW)
    )
    if not interesting:
        return None

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "hwnd": int(hwnd),
        "pid": int(pid.value),
        "process": process,
        "class": class_name,
        "rect": [rect.left, rect.top, rect.right, rect.bottom],
        "owner_hwnd": int(user32.GetWindow(hwnd, _GW_OWNER) or 0),
        "exstyle": exstyle,
        "foreground": int(hwnd) == int(foreground),
    }


def probe_windows_state() -> dict[str, Any]:
    """Take one metadata-only snapshot of the Windows input environment."""
    state: dict[str, Any] = {
        "captured_at": _now(),
        "platform": platform.platform(),
        "processes": [], "foreground": {}, "cursor": [0, 0],
        "modifiers": {"ctrl": False, "alt": False, "shift": False, "win": False},
        "windows": [],
    }
    if os.name != "nt":
        return state
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    foreground = int(user32.GetForegroundWindow() or 0)
    process_names = _process_names()
    state["processes"] = _running_processes(process_names)

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    if user32.GetCursorPos(ctypes.byref(point)):
        state["cursor"] = [point.x, point.y]
    state["modifiers"] = {
        "ctrl": bool(user32.GetAsyncKeyState(0x11) & 0x8000),
        "alt": bool(user32.GetAsyncKeyState(0x12) & 0x8000),
        "shift": bool(user32.GetAsyncKeyState(0x10) & 0x8000),
        "win": bool((user32.GetAsyncKeyState(0x5B) | user32.GetAsyncKeyState(0x5C)) & 0x8000),
    }

    windows: list[dict[str, Any]] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def callback(hwnd, _lparam):
        record = _window_record(int(hwnd), foreground, process_names)
        if record is not None:
            windows.append(record)
        return True

    user32.EnumWindows(callback, 0)
    state["windows"] = sorted(windows, key=lambda item: item["hwnd"])
    fg_record = _window_record(foreground, foreground, process_names) if foreground else None
    if fg_record is None:
        pid = ctypes.c_ulong()
        if foreground:
            user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        fg_record = {"hwnd": foreground, "pid": int(pid.value), "process": process_names.get(int(pid.value), "")}
    state["foreground"] = fg_record
    return state


def _integer_pair(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return [0, 0]
    return [int(value[0]), int(value[1])]


def _sanitize_window(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    rect = item.get("rect", [0, 0, 0, 0])
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        rect = [0, 0, 0, 0]
    return {
        "hwnd": int(item.get("hwnd", 0)), "pid": int(item.get("pid", 0)),
        "process": Path(str(item.get("process", ""))).name.lower(),
        "class": str(item.get("class", ""))[:128],
        "rect": [int(x) for x in rect],
        "owner_hwnd": int(item.get("owner_hwnd", 0)),
        "exstyle": int(item.get("exstyle", 0)),
        "foreground": bool(item.get("foreground", False)),
    }


def sanitize_state(value: Any) -> dict[str, Any]:
    """Whitelist diagnostic fields so injected probes cannot leak text."""
    state = value if isinstance(value, dict) else {}
    processes = []
    for item in state.get("processes", []):
        if isinstance(item, dict):
            processes.append({
                "pid": int(item.get("pid", 0)),
                "process": Path(str(item.get("process", ""))).name.lower(),
            })
    modifiers = state.get("modifiers", {}) if isinstance(state.get("modifiers"), dict) else {}
    foreground = _sanitize_window(state.get("foreground", {}))
    return {
        "captured_at": str(state.get("captured_at", _now())),
        "platform": str(state.get("platform", platform.platform()))[:256],
        "processes": processes,
        "foreground": foreground,
        "cursor": _integer_pair(state.get("cursor")),
        "modifiers": {key: bool(modifiers.get(key, False)) for key in ("ctrl", "alt", "shift", "win")},
        "windows": [_sanitize_window(item) for item in state.get("windows", [])],
    }


class InputDiagnosticsService(QObject):
    """Poll and persist a compact timeline for intermittent IME popups."""

    updated = Signal(dict)
    monitoringChanged = Signal(bool)
    _probeReady = Signal(dict)
    _probeFailed = Signal(str)

    def __init__(self, probe: Callable[[], dict[str, Any]] = probe_windows_state,
                 log_path: str | Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._probe = probe
        self.log_path = Path(log_path) if log_path else input_diagnostics_log_path()
        self._timer = QTimer(self)
        self._timer.setInterval(750)
        self._timer.timeout.connect(self._schedule_poll)
        self._probeReady.connect(self._accept_state)
        self._probeFailed.connect(self._accept_probe_error)
        self._probe_inflight = False
        self._events: deque[dict[str, Any]] = deque(maxlen=400)
        self._last_window_ids: set[int] = set()
        self._last_modifiers: tuple[str, ...] = ()
        self._last_foreground: tuple[int, int] = (0, 0)
        self._last_state = sanitize_state({})

    @property
    def monitoring(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self.monitoring:
            return
        self._timer.start()
        self.monitoringChanged.emit(True)
        self._schedule_poll()

    def stop(self) -> None:
        if not self.monitoring:
            return
        self._timer.stop()
        self.monitoringChanged.emit(False)

    def poll(self) -> None:
        """同步采集一次；保留给手动调用和确定性的单元测试。"""
        try:
            state = sanitize_state(self._probe())
        except Exception as exc:
            self._record("probe_error", {"error_type": type(exc).__name__})
            return
        self._accept_state(state)

    def _schedule_poll(self) -> None:
        """在工作线程采集，避免 Windows 枚举阻塞托盘和主窗口事件。"""
        if self._probe_inflight:
            return
        self._probe_inflight = True

        def run() -> None:
            try:
                self._probeReady.emit(sanitize_state(self._probe()))
            except Exception as exc:
                self._probeFailed.emit(type(exc).__name__)

        threading.Thread(
            target=run, name="rime-input-diagnostics", daemon=True,
        ).start()

    def _accept_probe_error(self, error_type: str) -> None:
        self._probe_inflight = False
        self._record("probe_error", {"error_type": str(error_type)[:128]})

    def _accept_state(self, state: dict[str, Any]) -> None:
        self._probe_inflight = False
        self._last_state = state
        ids = {item["hwnd"] for item in state["windows"]}
        appeared = [item for item in state["windows"] if item["hwnd"] not in self._last_window_ids]
        disappeared = sorted(self._last_window_ids - ids)
        if appeared:
            self._record("windows_appeared", {"windows": appeared})
        if disappeared:
            self._record("windows_disappeared", {"hwnds": disappeared})
        self._last_window_ids = ids

        active = tuple(key for key, down in state["modifiers"].items() if down)
        if active != self._last_modifiers:
            self._record("modifiers_changed", {"keys": list(active)})
            self._last_modifiers = active
        foreground = (state["foreground"]["hwnd"], state["foreground"]["pid"])
        if foreground != self._last_foreground:
            self._record("foreground_changed", {"foreground": state["foreground"]})
            self._last_foreground = foreground
        self.updated.emit(state)

    def capture_now(self, reason: str = "manual") -> dict[str, Any]:
        try:
            state = sanitize_state(self._probe())
        except Exception as exc:
            state = self._last_state
            self._record("probe_error", {"error_type": type(exc).__name__})
        self._last_state = state
        self._record("scene_capture", {"reason": str(reason)[:64], "state": state})
        self.updated.emit(state)
        return state

    def recent_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def recent_report(self) -> str:
        payload = {
            "privacy": "metadata-only; no titles, typed text, clipboard, or composition",
            "monitoring": self.monitoring,
            "latest_state": self._last_state,
            "events": list(self._events)[-100:],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _record(self, event: str, details: dict[str, Any]) -> None:
        item = {"at": _now(), "event": event, "details": details}
        self._events.append(item)
        self._append_log(item)

    def _append_log(self, item: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 2 * 1024 * 1024:
                rotated = self.log_path.with_suffix(self.log_path.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                self.log_path.replace(rotated)
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass


class InputDiagnosticsPlugin:
    """Small façade used by AppContext/MainWindow as a built-in plug-in."""

    plugin_id = "input-diagnostics"
    display_name = "输入诊断"

    def __init__(self, service: InputDiagnosticsService | None = None) -> None:
        self.service = service or InputDiagnosticsService()

    def start(self) -> None:
        self.service.start()

    def stop(self) -> None:
        self.service.stop()

    def capture_now(self, reason: str = "manual") -> dict[str, Any]:
        return self.service.capture_now(reason)

    def create_widget(self, parent=None):
        from src.ui.input_diagnostics_widget import InputDiagnosticsWidget
        return InputDiagnosticsWidget(self.service, parent)