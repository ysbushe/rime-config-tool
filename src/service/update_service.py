"""GitHub Releases updater for both packaged Windows distributions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from src.config.paths import user_config_dir

REPOSITORY_URL = "https://github.com/ysbushe/rime-config-tool"
_RELEASE_API = "https://api.github.com/repos/ysbushe/rime-config-tool/releases/latest"
_PACKAGE_ONEFILE = "onefile"
_PACKAGE_DIRECTORY = "directory"
_PACKAGE_SOURCE = "source"
_RELEASE_ASSETS = {
    _PACKAGE_ONEFILE: "RimeConfig.exe",
    _PACKAGE_DIRECTORY: "RimeConfig-portable.zip",
}


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    download_url: str
    asset_name: str
    sha256: str = ""
    package_kind: str = _PACKAGE_ONEFILE


def _version_tuple(value: str) -> tuple[int, ...]:
    clean = value.strip().lstrip("vV")
    return tuple(int(part) for part in clean.split(".") if part.isdigit())


def _asset_sha256(asset: dict) -> str:
    digest = str(asset.get("digest", ""))
    prefix, separator, value = digest.partition(":")
    return value.lower() if separator and prefix.lower() == "sha256" else ""


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class UpdateService:
    """Select and atomically install an asset matching the running package type."""

    @staticmethod
    def package_kind(executable: Path | None = None) -> str:
        if executable is None and not getattr(sys, "frozen", False):
            return _PACKAGE_SOURCE
        target = (executable or Path(sys.executable)).resolve()
        return _PACKAGE_DIRECTORY if (target.parent / "_internal").is_dir() else _PACKAGE_ONEFILE

    def latest_release(self, package_kind: str | None = None) -> ReleaseInfo | None:
        package_kind = package_kind or self.package_kind()
        asset_name = _RELEASE_ASSETS.get(package_kind)
        if not asset_name:
            return None
        request = urllib.request.Request(_RELEASE_API, headers={"User-Agent": "RimeConfigTool"})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for asset in payload.get("assets", []):
            name = str(asset.get("name", ""))
            if name.casefold() == asset_name.casefold():
                return ReleaseInfo(
                    str(payload.get("tag_name", "")), str(asset["browser_download_url"]),
                    name, _asset_sha256(asset), package_kind,
                )
        return None

    def check(self, current_version: str) -> tuple[ReleaseInfo | None, str]:
        package_kind = self.package_kind()
        if package_kind == _PACKAGE_SOURCE:
            return None, "当前为源码运行，不能直接覆盖更新。"
        try:
            release = self.latest_release(package_kind)
        except Exception as exc:
            return None, f"检查更新失败：{exc}"
        if release is None:
            wanted = _RELEASE_ASSETS[package_kind]
            return None, f"GitHub Releases 未找到适用于当前程序的 {wanted}。"
        if _version_tuple(release.version) <= _version_tuple(current_version):
            return None, f"已是最新版本 {current_version}。"
        label = "目录式包" if package_kind == _PACKAGE_DIRECTORY else "单文件包"
        return release, f"发现新版本 {release.version}，将更新{label}。"

    def download_replace_and_restart(
        self, release: ReleaseInfo, current_version: str = ""
    ) -> tuple[bool, str]:
        if not getattr(sys, "frozen", False):
            return False, "当前为源码运行，不能覆盖工作区；请从 GitHub Releases 下载更新包。"
        current_kind = self.package_kind()
        if release.package_kind != current_kind:
            return False, "更新包类型与当前程序不匹配，已停止替换。"
        try:
            if current_kind == _PACKAGE_ONEFILE:
                return self._update_onefile(release, current_version)
            if current_kind == _PACKAGE_DIRECTORY:
                return self._update_directory(release, current_version)
        except Exception as exc:
            return False, f"更新准备失败：{exc}"
        return False, "无法识别当前发布包类型。"

    def _download(self, release: ReleaseInfo, destination: Path) -> None:
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        try:
            digest = hashlib.sha256()
            request = urllib.request.Request(release.download_url, headers={"User-Agent": "RimeConfigTool"})
            with urllib.request.urlopen(request, timeout=30) as response:
                with partial.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
            if release.sha256 and digest.hexdigest().lower() != release.sha256.lower():
                raise RuntimeError("下载文件校验失败，未替换当前程序。")
            os.replace(partial, destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise

    def _update_onefile(
        self, release: ReleaseInfo, current_version: str
    ) -> tuple[bool, str]:
        target = Path(sys.executable).resolve()
        staged = target.with_name(target.stem + ".update.exe")
        self._download(release, staged)
        backup = target.with_name(target.stem + ".backup.exe")
        log_path = target.with_name(target.stem + ".update.log")
        updates = user_config_dir() / "updates"
        script = updates / "RimeConfig-onefile-update.ps1"
        ready = updates / "RimeConfig-onefile-update.ready"
        complete = updates / "RimeConfig-onefile-update.complete"
        launch_log = updates / "RimeConfig-onefile-handoff.log"
        updates.mkdir(parents=True, exist_ok=True)
        ready.unlink(missing_ok=True)
        complete.unlink(missing_ok=True)
        self._write_powershell_script(
            script,
            self._handoff_script(
                target, staged, backup, log_path, ready, complete,
                os.getpid(), current_version, release.version,
            ),
        )
        self._start_handoff(script, ready, launch_log)
        return True, "单文件更新已下载，当前程序退出后将自动替换并重启。"

    def _update_directory(
        self, release: ReleaseInfo, current_version: str
    ) -> tuple[bool, str]:
        target_exe = Path(sys.executable).resolve()
        target_dir = target_exe.parent
        updates = user_config_dir() / "updates"
        updates.mkdir(parents=True, exist_ok=True)
        archive = updates / f"RimeConfig-{release.version}-portable.zip"
        self._download(release, archive)
        stage_root = updates / f"RimeConfig-stage-{release.version}"
        if stage_root.exists():
            shutil.rmtree(stage_root)
        stage_root.mkdir(parents=True)
        try:
            self._extract_package(archive, stage_root)
            staged_dir = self._find_package_root(stage_root)
        except Exception:
            shutil.rmtree(stage_root, ignore_errors=True)
            raise
        backup_dir = target_dir.with_name(target_dir.name + ".backup")
        log_path = updates / "RimeConfig-directory-update.log"
        script = updates / "RimeConfig-directory-update.ps1"
        ready = updates / "RimeConfig-directory-update.ready"
        complete = updates / "RimeConfig-directory-update.complete"
        launch_log = updates / "RimeConfig-directory-handoff.log"
        ready.unlink(missing_ok=True)
        complete.unlink(missing_ok=True)
        self._write_powershell_script(
            script,
            self._directory_handoff_script(
                target_dir, staged_dir, backup_dir, log_path, ready, complete,
                os.getpid(), current_version, release.version,
            ),
        )
        self._start_handoff(script, ready, launch_log)
        return True, "目录式更新已下载，当前程序退出后将整体替换目录并重启。"

    @staticmethod
    def _extract_package(archive: Path, stage_root: Path) -> None:
        with zipfile.ZipFile(archive) as package:
            root = stage_root.resolve()
            for item in package.infolist():
                destination = (stage_root / item.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise RuntimeError("更新压缩包包含非法路径。")
            package.extractall(stage_root)

    @staticmethod
    def _find_package_root(stage_root: Path) -> Path:
        candidates = [
            path.parent for path in stage_root.rglob("RimeConfig.exe")
            if (path.parent / "_internal").is_dir()
        ]
        if len(candidates) != 1:
            raise RuntimeError("更新压缩包不是完整的目录式发布包。")
        return candidates[0]

    @staticmethod
    def _write_powershell_script(script: Path, content: str) -> None:
        """Windows PowerShell 5.1 needs a BOM to decode non-ASCII scripts as UTF-8."""
        script.write_text(content, encoding="utf-8-sig")

    @staticmethod
    def _powershell_executable() -> str:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable:
            return executable
        raise RuntimeError("未找到 Windows PowerShell，无法启动更新器。")

    @classmethod
    def _start_handoff(
        cls, script: Path, ready_marker: Path, launch_log: Path,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Start the updater and wait until its fully parsed script acknowledges control."""
        launch_log.parent.mkdir(parents=True, exist_ok=True)
        with launch_log.open("ab") as output:
            process = subprocess.Popen(
                [
                    cls._powershell_executable(), "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                    "-File", str(script),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                cwd=str(script.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if ready_marker.is_file():
                ready_marker.unlink(missing_ok=True)
                return
            return_code = process.poll()
            if return_code is not None:
                detail = ""
                try:
                    detail = launch_log.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    pass
                suffix = f"：{detail[-800:]}" if detail else ""
                raise RuntimeError(f"更新器启动失败（退出码 {return_code}）{suffix}")
            time.sleep(0.05)
        process.terminate()
        raise RuntimeError("更新器未在限定时间内确认接管，当前程序保持运行。")

    @staticmethod
    def _handoff_script(
        target: Path, staged: Path, backup: Path, log_path: Path,
        ready_marker: Path, complete_marker: Path, old_pid: int,
        from_version: str, to_version: str,
    ) -> str:
        target_q = _ps_quote(str(target))
        staged_q = _ps_quote(str(staged))
        backup_q = _ps_quote(str(backup))
        log_q = _ps_quote(str(log_path))
        ready_q = _ps_quote(str(ready_marker))
        complete_q = _ps_quote(str(complete_marker))
        from_q = _ps_quote(from_version)
        to_q = _ps_quote(to_version)
        return f"""$ErrorActionPreference = 'Stop'
$target = {target_q}
$staged = {staged_q}
$backup = {backup_q}
$log = {log_q}
$ready = {ready_q}
$complete = {complete_q}
$fromVersion = {from_q}
$toVersion = {to_q}
$oldPid = {old_pid}
function Write-UpdateLog([string]$message) {{
  try {{ "$(Get-Date -Format o) $message" | Out-File -LiteralPath $log -Append -Encoding utf8 }} catch {{}}
}}
Set-Content -LiteralPath $ready -Value 'ready' -Encoding ascii
try {{
  while (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 250 }}
  if (-not (Test-Path -LiteralPath $staged)) {{ throw '更新文件不存在。' }}
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
  Move-Item -LiteralPath $target -Destination $backup -Force
  Move-Item -LiteralPath $staged -Destination $target -Force
  $completeName = [System.IO.Path]::GetFileName($complete)
  $launchArgs = @('--update-completed', $fromVersion, $toVersion, '--update-marker', $completeName)
  $newProcess = Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -ArgumentList $launchArgs -PassThru
  Start-Sleep -Seconds 3
  if ($newProcess.HasExited) {{ throw '新版本启动后立即退出。' }}
  Write-UpdateLog '更新替换并重启成功。'
  Set-Content -LiteralPath $complete -Value 'success' -Encoding ascii
  try {{ Remove-Item -LiteralPath $backup -Force }} catch {{ Write-UpdateLog ("旧版本清理延后：" + $_.Exception.Message) }}
}} catch {{
  Write-UpdateLog ("更新失败：" + $_.Exception.Message)
  try {{
    if ($newProcess -and -not $newProcess.HasExited) {{
      Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue
      Wait-Process -Id $newProcess.Id -Timeout 5 -ErrorAction SilentlyContinue
    }}
    if ((Test-Path -LiteralPath $backup) -and (Test-Path -LiteralPath $target)) {{ Remove-Item -LiteralPath $target -Force }}
    if (Test-Path -LiteralPath $backup) {{ Move-Item -LiteralPath $backup -Destination $target -Force }}
    if (Test-Path -LiteralPath $target) {{ Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) }}
    Write-UpdateLog '已恢复旧版本。'
  }} catch {{ Write-UpdateLog ("恢复旧版本失败：" + $_.Exception.Message) }}
}} finally {{
  Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""

    @staticmethod
    def _directory_handoff_script(
        target_dir: Path, staged_dir: Path, backup_dir: Path, log_path: Path,
        ready_marker: Path, complete_marker: Path, old_pid: int,
        from_version: str, to_version: str,
    ) -> str:
        target_q = _ps_quote(str(target_dir))
        staged_q = _ps_quote(str(staged_dir))
        backup_q = _ps_quote(str(backup_dir))
        log_q = _ps_quote(str(log_path))
        target_exe_q = _ps_quote(str(target_dir / "RimeConfig.exe"))
        ready_q = _ps_quote(str(ready_marker))
        complete_q = _ps_quote(str(complete_marker))
        from_q = _ps_quote(from_version)
        to_q = _ps_quote(to_version)
        return f"""$ErrorActionPreference = 'Stop'
$targetDir = {target_q}
$stagedDir = {staged_q}
$backupDir = {backup_q}
$targetExe = {target_exe_q}
$log = {log_q}
$ready = {ready_q}
$complete = {complete_q}
$fromVersion = {from_q}
$toVersion = {to_q}
$oldPid = {old_pid}
function Write-UpdateLog([string]$message) {{
  try {{ "$(Get-Date -Format o) $message" | Out-File -LiteralPath $log -Append -Encoding utf8 }} catch {{}}
}}
Set-Content -LiteralPath $ready -Value 'ready' -Encoding ascii
try {{
  while (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 250 }}
  Start-Sleep -Milliseconds 800
  if (-not (Test-Path -LiteralPath (Join-Path $stagedDir 'RimeConfig.exe'))) {{ throw '目录式更新文件不完整。' }}
  if (Test-Path -LiteralPath $backupDir) {{ Remove-Item -LiteralPath $backupDir -Recurse -Force }}
  Move-Item -LiteralPath $targetDir -Destination $backupDir -Force
  Move-Item -LiteralPath $stagedDir -Destination $targetDir -Force
  $completeName = [System.IO.Path]::GetFileName($complete)
  $launchArgs = @('--update-completed', $fromVersion, $toVersion, '--update-marker', $completeName)
  $newProcess = Start-Process -FilePath $targetExe -WorkingDirectory $targetDir -ArgumentList $launchArgs -PassThru
  Start-Sleep -Seconds 3
  if ($newProcess.HasExited) {{ throw '新版本启动后立即退出。' }}
  Write-UpdateLog '目录式更新替换并重启成功。'
  Set-Content -LiteralPath $complete -Value 'success' -Encoding ascii
  try {{ Remove-Item -LiteralPath $backupDir -Recurse -Force }} catch {{ Write-UpdateLog ("旧目录清理延后：" + $_.Exception.Message) }}
}} catch {{
  Write-UpdateLog ("目录式更新失败：" + $_.Exception.Message)
  try {{
    if ($newProcess -and -not $newProcess.HasExited) {{
      Stop-Process -Id $newProcess.Id -Force -ErrorAction SilentlyContinue
      Wait-Process -Id $newProcess.Id -Timeout 5 -ErrorAction SilentlyContinue
    }}
    if ((Test-Path -LiteralPath $backupDir) -and (Test-Path -LiteralPath $targetDir)) {{ Remove-Item -LiteralPath $targetDir -Recurse -Force }}
    if (Test-Path -LiteralPath $backupDir) {{ Move-Item -LiteralPath $backupDir -Destination $targetDir -Force }}
    if (Test-Path -LiteralPath $targetExe) {{ Start-Process -FilePath $targetExe -WorkingDirectory $targetDir }}
    Write-UpdateLog '已恢复旧目录版本。'
  }} catch {{ Write-UpdateLog ("恢复旧目录版本失败：" + $_.Exception.Message) }}
}} finally {{
  Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""
