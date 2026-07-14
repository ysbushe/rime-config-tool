"""GitHub Releases updater for packaged Windows builds."""
from __future__ import annotations

import json
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


def _version_tuple(value: str) -> tuple[int, ...]:
    clean = value.strip().lstrip("vV")
    return tuple(int(part) for part in clean.split(".") if part.isdigit())


class UpdateService:
    def latest_release(self) -> ReleaseInfo | None:
        request = urllib.request.Request(_RELEASE_API, headers={"User-Agent": "RimeConfigTool"})
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for asset in payload.get("assets", []):
            name = str(asset.get("name", ""))
            if name.lower().endswith(".exe"):
                return ReleaseInfo(str(payload.get("tag_name", "")), str(asset["browser_download_url"]), name)
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
            request = urllib.request.Request(release.download_url, headers={"User-Agent": "RimeConfigTool"})
            with urllib.request.urlopen(request, timeout=30) as response:
                with partial.open("wb") as handle:
                    while chunk := response.read(1024 * 1024):
                        handle.write(chunk)
            partial.replace(staged)
        except Exception as exc:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            return False, f"下载更新失败：{exc}"
        command = (
            "Start-Sleep -Seconds 2; "
            f"Copy-Item -LiteralPath '{staged}' -Destination '{target}' -Force; "
            f"Start-Process -FilePath '{target}'"
        )
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command])
        return True, "更新已下载，正在替换并重启。"
