"""GitHub Releases updater for packaged Windows builds."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_URL = "https://github.com/ysbushe/rime-config-tool"
_RELEASE_API = "https://api.github.com/repos/ysbushe/rime-config-tool/releases/latest"


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    download_url: str
    asset_name: str
    sha256: str = ""


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
    def latest_release(self) -> ReleaseInfo | None:
        request = urllib.request.Request(_RELEASE_API, headers={"User-Agent": "RimeConfigTool"})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for asset in payload.get("assets", []):
            name = str(asset.get("name", ""))
            if name.lower().endswith(".exe"):
                return ReleaseInfo(
                    str(payload.get("tag_name", "")), str(asset["browser_download_url"]),
                    name, _asset_sha256(asset),
                )
        return None

    def check(self, current_version: str) -> tuple[ReleaseInfo | None, str]:
        try:
            release = self.latest_release()
        except Exception as exc:
            return None, f"检查更新失败：{exc}"
        if release is None:
            return None, "GitHub Releases 暂无可用 Windows 安装包。"
        if _version_tuple(release.version) <= _version_tuple(current_version):
            return None, f"已是最新版本 {current_version}。"
        return release, f"发现新版本 {release.version}。"

    def download_replace_and_restart(self, release: ReleaseInfo) -> tuple[bool, str]:
        if not getattr(sys, "frozen", False):
            return False, "当前为源码运行，不能覆盖工作区；请从 GitHub Releases 下载更新包。"
        target = Path(sys.executable).resolve()
        staged = target.with_name(target.stem + ".update.exe")
        partial = staged.with_suffix(staged.suffix + ".part")
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
            os.replace(partial, staged)
        except Exception as exc:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            return False, f"下载更新失败：{exc}"

        backup = target.with_name(target.stem + ".backup.exe")
        log_path = target.with_name(target.stem + ".update.log")
        script = target.with_name(target.stem + ".update.ps1")
        script.write_text(self._handoff_script(target, staged, backup, log_path, os.getpid()), encoding="utf-8")
        try:
            subprocess.Popen([
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", str(script),
            ])
        except Exception as exc:
            return False, f"无法启动更新交接程序：{exc}"
        return True, "更新已下载，正在等待当前程序退出后安全替换并重启。"

    @staticmethod
    def _handoff_script(target: Path, staged: Path, backup: Path, log_path: Path, old_pid: int) -> str:
        target_q = _ps_quote(str(target))
        staged_q = _ps_quote(str(staged))
        backup_q = _ps_quote(str(backup))
        log_q = _ps_quote(str(log_path))
        return f"""$ErrorActionPreference = 'Stop'
$target = {target_q}
$staged = {staged_q}
$backup = {backup_q}
$log = {log_q}
$oldPid = {old_pid}
function Write-UpdateLog([string]$message) {{
  "$(Get-Date -Format o) $message" | Out-File -LiteralPath $log -Append -Encoding utf8
}}
try {{
  while (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 250 }}
  if (-not (Test-Path -LiteralPath $staged)) {{ throw '更新文件不存在。' }}
  if (Test-Path -LiteralPath $backup) {{ Remove-Item -LiteralPath $backup -Force }}
  Move-Item -LiteralPath $target -Destination $backup -Force
  Move-Item -LiteralPath $staged -Destination $target -Force
  $newProcess = Start-Process -FilePath $target -PassThru
  Start-Sleep -Seconds 3
  if ($newProcess.HasExited) {{ throw '新版本启动后立即退出。' }}
  Remove-Item -LiteralPath $backup -Force
  Write-UpdateLog '更新替换并重启成功。'
}} catch {{
  Write-UpdateLog ("更新失败：" + $_.Exception.Message)
  try {{
    if ((Test-Path -LiteralPath $backup) -and (Test-Path -LiteralPath $target)) {{ Remove-Item -LiteralPath $target -Force }}
    if (Test-Path -LiteralPath $backup) {{ Move-Item -LiteralPath $backup -Destination $target -Force }}
    if (Test-Path -LiteralPath $target) {{ Start-Process -FilePath $target }}
    Write-UpdateLog '已恢复旧版本。'
  }} catch {{ Write-UpdateLog ("恢复旧版本失败：" + $_.Exception.Message) }}
}} finally {{
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}}
"""
