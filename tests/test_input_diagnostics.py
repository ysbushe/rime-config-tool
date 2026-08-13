from __future__ import annotations

import json
from pathlib import Path
import threading
import time

from src import __version__
from src.plugins.input_diagnostics import InputDiagnosticsService
from src.ui.input_diagnostics_widget import InputDiagnosticsWidget
from src.diagnostics_main import InputDiagnosticsWindow
from src.service.input_diagnostics_launcher import (
    diagnostics_command,
    diagnostics_working_directory,
)


def _state(*, windows=None, modifiers=None):
    return {
        "captured_at": "2026-07-27T10:00:00+08:00",
        "platform": "Windows-test",
        "processes": [{"pid": 12, "process": "C:/Program Files/Rime/weaselserver.exe"}],
        "foreground": {
            "hwnd": 100, "pid": 33, "process": "editor.exe", "class": "EditorClass",
            "rect": [0, 0, 800, 600], "owner_hwnd": 0, "exstyle": 0,
            "foreground": True, "title": "PRIVATE DOCUMENT TITLE",
        },
        "cursor": [120, 80],
        "modifiers": modifiers or {},
        "windows": windows or [],
        "typed_text": "DO-NOT-LOG-THIS",
        "clipboard": "SECRET-CLIPBOARD",
    }


def test_diagnostics_tracks_popup_lifecycle_without_text(tmp_path, qapp) -> None:
    popup = {
        "hwnd": 501, "pid": 12, "process": "weaselserver.exe", "class": "#32768",
        "rect": [20, 30, 120, 500], "owner_hwnd": 100, "exstyle": 128,
        "foreground": False, "title": "SECRET MENU TEXT",
    }
    states = iter([
        _state(windows=[popup]),
        _state(windows=[], modifiers={"alt": True}),
        _state(windows=[]),
    ])
    service = InputDiagnosticsService(probe=lambda: next(states), log_path=tmp_path / "events.jsonl")

    service.poll()
    service.poll()
    service.capture_now("test")

    events = service.recent_events()
    names = [item["event"] for item in events]
    assert "windows_appeared" in names
    assert "windows_disappeared" in names
    assert "modifiers_changed" in names
    assert "scene_capture" in names

    report = service.recent_report()
    assert "DO-NOT-LOG-THIS" not in report
    assert "SECRET-CLIPBOARD" not in report
    assert "PRIVATE DOCUMENT TITLE" not in report
    assert "SECRET MENU TEXT" not in report
    assert '"title"' not in report
    assert json.loads(report)["latest_state"]["processes"][0]["process"] == "weaselserver.exe"
    assert (tmp_path / "events.jsonl").is_file()


def test_diagnostics_widget_builds_as_standalone_content(qapp, tmp_path) -> None:
    service = InputDiagnosticsService(probe=lambda: _state(), log_path=tmp_path / "events.jsonl")
    widget = InputDiagnosticsWidget(service)
    widget.refresh(service.capture_now("test"))
    assert "未检测到" not in widget._processes.text()
    assert widget._btn_capture.text() == "记录当前现场"


def test_diagnostics_gui_test_launch_is_visibly_marked(qapp, monkeypatch) -> None:
    monkeypatch.setenv("RIME_CONFIG_GUI_TEST", "1")
    monkeypatch.setattr(InputDiagnosticsService, "start", lambda _self: None)
    monkeypatch.setattr(InputDiagnosticsService, "stop", lambda _self: None)
    window = InputDiagnosticsWindow()
    try:
        assert window.windowTitle().endswith(f"v{__version__} · GUI测试中")
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_source_launcher_uses_separate_module_process(monkeypatch) -> None:
    import sys

    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "C:/Python/python.exe")
    program, arguments = diagnostics_command()

    assert program == "C:/Python/python.exe"
    assert arguments == ["-m", "src.diagnostics_main"]
    working_directory = diagnostics_working_directory(program)
    assert (Path(working_directory) / 'src' / 'diagnostics_main.py').is_file()

def test_periodic_diagnostics_probe_does_not_block_gui_thread(qapp, tmp_path) -> None:
    release = threading.Event()

    def slow_probe():
        release.wait(1)
        return _state()

    service = InputDiagnosticsService(
        probe=slow_probe, log_path=tmp_path / "events.jsonl",
    )
    started = time.perf_counter()
    service._schedule_poll()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert service._probe_inflight
    release.set()
    for _ in range(50):
        qapp.processEvents()
        if not service._probe_inflight:
            break
        time.sleep(0.01)
    assert not service._probe_inflight
