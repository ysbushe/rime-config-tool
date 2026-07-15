from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QSystemTrayIcon

from src.service.update_service import UpdateService, _asset_sha256
from src.ui.main_window import MainWindow
from src.ui.tray_icon import TrayIcon
from src.utils.encoding import write_text_utf8


def test_atomic_text_write_replaces_content_without_bom(tmp_path: Path) -> None:
    path = tmp_path / "config.txt"
    write_text_utf8(path, "first")
    write_text_utf8(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_text_write_preserves_original_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "config.txt"
    path.write_text("stable", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("locked")

    monkeypatch.setattr("src.utils.encoding.os.replace", fail_replace)
    with pytest.raises(OSError, match="locked"):
        write_text_utf8(path, "new")

    assert path.read_text(encoding="utf-8") == "stable"
    assert not list(tmp_path.glob("*.tmp"))


def test_update_handoff_waits_for_old_process_and_has_rollback(tmp_path: Path) -> None:
    service = UpdateService()
    target = tmp_path / "RimeConfig.exe"
    script = service._handoff_script(
        target, tmp_path / "RimeConfig.update.exe", tmp_path / "RimeConfig.backup.exe",
        tmp_path / "RimeConfig.update.log", 1234,
    )

    assert "Get-Process -Id $oldPid" in script
    assert "RimeConfig.backup.exe" in script
    assert "已恢复旧版本" in script
    assert "新版本启动后立即退出" in script


def test_release_digest_parsing() -> None:
    assert _asset_sha256({"digest": "sha256:ABC"}) == "abc"
    assert _asset_sha256({"digest": "md5:abc"}) == ""


def test_tray_trigger_fallback_and_double_click(qapp) -> None:
    tray = TrayIcon()
    received = QSignalSpy(tray.requestOpen)
    trigger = QSystemTrayIcon.ActivationReason.Trigger
    double_click = QSystemTrayIcon.ActivationReason.DoubleClick

    tray._on_activated(double_click)
    assert received.count() == 1
    tray._on_activated(trigger)
    assert tray._trigger_timer.isActive()
    tray._trigger_timer.timeout.emit()
    assert received.count() == 2


def test_show_main_restores_and_foregrounds_window() -> None:
    calls: list[str] = []
    fake = SimpleNamespace(
        _start_optional_services=lambda: calls.append("services"),
        show=lambda: calls.append("show"),
        showNormal=lambda: calls.append("normal"),
        raise_=lambda: calls.append("raise"),
        activateWindow=lambda: calls.append("activate"),
    )

    MainWindow.show_main(fake)

    assert calls == ["services", "show", "normal", "raise", "activate"]
