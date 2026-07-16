from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

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
        tmp_path / "RimeConfig", package_root, tmp_path / "RimeConfig.backup", tmp_path / "update.log", 1234,
    )

    assert package_root.name == "RimeConfig"
    assert "Move-Item -LiteralPath $targetDir -Destination $backupDir" in script
    assert "已恢复旧目录版本" in script


def test_update_refuses_mismatched_package_type(monkeypatch, tmp_path: Path) -> None:
    exe = tmp_path / "RimeConfig.exe"
    exe.write_bytes(b"exe")
    monkeypatch.setattr("src.service.update_service.sys.frozen", True, raising=False)
    monkeypatch.setattr("src.service.update_service.sys.executable", str(exe))
    release = ReleaseInfo("9.9.9", "https://example/dir.zip", "RimeConfig-portable.zip", package_kind=_PACKAGE_DIRECTORY)

    ok, message = UpdateService().download_replace_and_restart(release)

    assert ok is False
    assert "不匹配" in message
