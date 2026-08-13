from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.service.autostart import Autostart
from src.service.update_service import (
    _PACKAGE_DIRECTORY,
    _PACKAGE_ONEFILE,
    ReleaseInfo,
    UpdateService,
)


def _autostart(tmp_path: Path) -> Autostart:
    service = Autostart()
    service._startup = tmp_path / "Startup"
    service._link = service._startup / "RimeConfig.lnk"
    service._startup.mkdir()
    return service


def test_autostart_bat_requires_current_target_and_argument(tmp_path: Path) -> None:
    service = _autostart(tmp_path)
    target = tmp_path / "RimeConfig.exe"
    target.write_bytes(b"exe")
    bat = service._link.with_suffix(".bat")
    bat.write_text(f'@echo off\r\nstart "" "{target}" --autostart\r\n', encoding="utf-8")

    status = service.status(str(target))

    assert status.enabled is True
    assert status.reason == "已正确启用"


def test_autostart_marks_stale_or_incomplete_bat_disabled(tmp_path: Path) -> None:
    service = _autostart(tmp_path)
    target = tmp_path / "RimeConfig.exe"
    target.write_bytes(b"exe")
    old_target = tmp_path / "Old" / "RimeConfig.exe"
    bat = service._link.with_suffix(".bat")
    bat.write_text(f'start "" "{old_target}" --autostart\n', encoding="utf-8")

    assert service.status(str(target)).enabled is False
    assert service.status(str(target)).reason == "自启项指向旧程序位置"

    bat.write_text(f'start "" "{target}"\n', encoding="utf-8")
    assert service.status(str(target)).reason == "自启项缺少最小化启动参数"


def test_autostart_reports_windows_disabled_entry(tmp_path: Path, monkeypatch) -> None:
    service = _autostart(tmp_path)
    target = tmp_path / "RimeConfig.exe"
    target.write_bytes(b"exe")
    bat = service._link.with_suffix(".bat")
    bat.write_text(f'start "" "{target}" --autostart\n', encoding="utf-8")
    monkeypatch.setattr(service, "_startup_approval_state", lambda _name: "disabled")

    status = service.status(str(target))

    assert status.enabled is False
    assert status.reason == "Windows 已禁用此启动项"


def test_autostart_enable_marks_windows_entry_enabled(tmp_path: Path, monkeypatch) -> None:
    service = _autostart(tmp_path)
    target = tmp_path / "RimeConfig.exe"
    target.write_bytes(b"exe")
    approved: list[str] = []

    def create_bat(target_path: str, link_path: str) -> None:
        Path(link_path).with_suffix(".bat").write_text(
            f'start "" "{target_path}" --autostart\n', encoding="utf-8")

    monkeypatch.setattr(service, "_create_shortcut", create_bat)
    monkeypatch.setattr(
        service, "_set_startup_approval_enabled", lambda name: approved.append(name) or True)

    assert service.enable(str(target)) is True
    assert approved == ["RimeConfig.bat"]


def test_autostart_repairs_moved_target_when_requested(tmp_path: Path, monkeypatch) -> None:
    service = _autostart(tmp_path)
    target = tmp_path / "New" / "RimeConfig.exe"
    target.parent.mkdir()
    target.write_bytes(b"exe")
    old_target = tmp_path / "Old" / "RimeConfig.exe"
    service._link.with_suffix(".bat").write_text(
        f'start "" "{old_target}" --autostart\n', encoding="utf-8")

    def create_bat(target_path: str, link_path: str) -> None:
        Path(link_path).with_suffix(".bat").write_text(
            f'start "" "{target_path}" --autostart\n', encoding="utf-8")

    monkeypatch.setattr(service, "_create_shortcut", create_bat)
    monkeypatch.setattr(service, "_set_startup_approval_enabled", lambda _name: True)

    status = service.repair_if_requested(True, str(target))

    assert status.enabled is True
    assert status.target == str(target)


def test_package_kind_detects_directory_and_onefile(tmp_path: Path) -> None:
    directory_exe = tmp_path / "RimeConfig" / "RimeConfig.exe"
    directory_exe.parent.mkdir(parents=True)
    directory_exe.write_bytes(b"exe")
    (directory_exe.parent / "_internal").mkdir()
    onefile_exe = tmp_path / "RimeConfig.exe"
    onefile_exe.write_bytes(b"exe")

    assert UpdateService.package_kind(directory_exe) == _PACKAGE_DIRECTORY
    assert UpdateService.package_kind(onefile_exe) == _PACKAGE_ONEFILE


def test_latest_release_selects_exact_asset_for_current_package(monkeypatch) -> None:
    payload = {
        "tag_name": "v9.9.9",
        "assets": [
            {"name": "RimeConfig.exe", "browser_download_url": "https://example/one.exe"},
            {"name": "RimeConfig-portable.zip", "browser_download_url": "https://example/dir.zip"},
        ],
    }

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("src.service.update_service.urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    service = UpdateService()

    assert service.latest_release(_PACKAGE_ONEFILE).asset_name == "RimeConfig.exe"
    assert service.latest_release(_PACKAGE_DIRECTORY).asset_name == "RimeConfig-portable.zip"


def test_directory_package_validation_and_handoff(tmp_path: Path) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("RimeConfig/RimeConfig.exe", b"exe")
        package.writestr("RimeConfig/_internal/python313.dll", b"dll")
    stage = tmp_path / "stage"
    stage.mkdir()

    UpdateService._extract_package(archive, stage)
    package_root = UpdateService._find_package_root(stage)
    script = UpdateService._directory_handoff_script(
        tmp_path / "RimeConfig", package_root, tmp_path / "RimeConfig.backup",
        tmp_path / "update.log", tmp_path / "update.ready",
        tmp_path / "update.complete", 1234, "0.10.0", "v0.10.1",
    )

    assert package_root.name == "RimeConfig"
    assert "Move-Item -LiteralPath $targetDir -Destination $backupDir" in script
    assert "已恢复旧目录版本" in script
    assert "--update-completed" in script
    assert "Set-Content -LiteralPath $complete" in script


def test_update_script_is_utf8_bom_and_parses_in_windows_powershell_51(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "中文目录" / "RimeConfig-directory-update.ps1"
    script_path.parent.mkdir()
    content = UpdateService._directory_handoff_script(
        tmp_path / "安装目录", tmp_path / "暂存目录", tmp_path / "备份目录",
        tmp_path / "更新日志.log", tmp_path / "接管.ready",
        tmp_path / "RimeConfig-test.complete", 1234, "0.10.0", "v0.10.1",
    )

    UpdateService._write_powershell_script(script_path, content)

    assert script_path.read_bytes().startswith(b"\xef\xbb\xbf")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is unavailable")
    quoted = str(script_path).replace("'", "''")
    command = (
        "$tokens=$null; $errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{quoted}',"
        "[ref]$tokens,[ref]$errors)|Out-Null; "
        "if($errors.Count){$errors|ForEach-Object{$_.Message};exit 1}"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=10, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_handoff_requires_ready_acknowledgement(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "update.ps1"
    script.write_text("# test", encoding="utf-8")
    ready = tmp_path / "update.ready"
    ready.write_text("ready", encoding="ascii")
    launch_log = tmp_path / "handoff.log"

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(UpdateService, "_powershell_executable", staticmethod(lambda: "powershell.exe"))
    monkeypatch.setattr("src.service.update_service.subprocess.Popen", lambda *_args, **_kwargs: Process())

    UpdateService._start_handoff(script, ready, launch_log, timeout_seconds=0.1)

    assert not ready.exists()


def test_handoff_process_failure_is_reported(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "update.ps1"
    script.write_text("# test", encoding="utf-8")
    ready = tmp_path / "update.ready"
    launch_log = tmp_path / "handoff.log"

    class Process:
        def poll(self):
            return 1

    monkeypatch.setattr(UpdateService, "_powershell_executable", staticmethod(lambda: "powershell.exe"))
    monkeypatch.setattr("src.service.update_service.subprocess.Popen", lambda *_args, **_kwargs: Process())

    with pytest.raises(RuntimeError, match="更新器启动失败"):
        UpdateService._start_handoff(script, ready, launch_log, timeout_seconds=0.1)


def test_update_refuses_mismatched_package_type(monkeypatch, tmp_path: Path) -> None:
    exe = tmp_path / "RimeConfig.exe"
    exe.write_bytes(b"exe")
    monkeypatch.setattr("src.service.update_service.sys.frozen", True, raising=False)
    monkeypatch.setattr("src.service.update_service.sys.executable", str(exe))
    release = ReleaseInfo("9.9.9", "https://example/dir.zip", "RimeConfig-portable.zip", package_kind=_PACKAGE_DIRECTORY)

    ok, message = UpdateService().download_replace_and_restart(release)

    assert ok is False
    assert "不匹配" in message
