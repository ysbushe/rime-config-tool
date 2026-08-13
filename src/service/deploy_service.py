"""部署服务（DeployService）。

调用小狼毫部署器 WeaselDeployer.exe /deploy 触发重新部署。
探测顺序：
    1) 用户在设置中手动指定的路径（deployer_path）优先；
    2) 注册表 WeaselRoot；
    3) 扩展递归搜索：ProgramFiles / ProgramFiles(x86) / LOCALAPPDATA / APPDATA /
       Rime 目录父级 / 常见安装盘（Program Files、Rime）下的 WeaselDeployer.exe。
若全部不可用则降级提示用户手动部署（不影响本地改动，因改动前已备份）。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

_FALLBACK_HINT = "请右键小狼毫托盘 → 重新部署"
_SERVER_READY_TIMEOUT_SECONDS = 18.0
_SERVER_READY_POLL_SECONDS = 0.5
_SERVER_STABLE_POLLS = 3


class DeployService:
    """触发 Rime 重新部署。"""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._exe = self._detect()
        self._version = self._detect_version(self._exe)

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._exe is not None

    @property
    def deployer_path(self) -> Optional[str]:
        return self._exe

    @property
    def weasel_version(self) -> str:
        return self._version

    def redetect(self) -> None:
        """重新探测部署器路径（如设置页『重新探测』触发）。"""
        self._exe = self._detect()
        self._version = self._detect_version(self._exe)

    @staticmethod
    def _detect_version(exe: Optional[str]) -> str:
        if not exe:
            return ""
        try:
            import win32api  # type: ignore
            info = win32api.GetFileVersionInfo(str(exe), "\\")
            ms = int(info.get("FileVersionMS", 0))
            ls = int(info.get("FileVersionLS", 0))
            parts = (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
            return ".".join(str(part) for part in parts).rstrip(".0")
        except Exception:
            parent = Path(exe).parent.name
            if parent.lower().startswith("weasel-"):
                return parent[7:]
        return ""

    # ------------------------------------------------------------------ #
    def deploy(self) -> Tuple[bool, str]:
        """触发部署，并等待小狼毫算法服务稳定。返回 (成功, 提示信息)。"""
        if bool(getattr(self._settings, "sandbox_mode", False)):
            msg = "沙盒模式，未触发真实部署。"
            logger.info(msg)
            return False, msg
        if not self._exe:
            msg = f"未找到 WeaselDeployer.exe，{_FALLBACK_HINT}"
            logger.warning(msg)
            return False, msg
        try:
            logger.info("触发部署：%s /deploy", self._exe)
            subprocess.run(
                [self._exe, "/deploy"],
                check=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=120,
            )
            ready, detail = self._wait_for_server_ready()
            if not ready:
                logger.warning(detail)
                return False, detail
            return True, detail
        except subprocess.TimeoutExpired:
            return False, f"部署超时，{_FALLBACK_HINT}"
        except Exception as exc:
            msg = f"部署失败：{exc}；{_FALLBACK_HINT}"
            logger.warning(msg)
            return False, msg

    def _wait_for_server_ready(self) -> Tuple[bool, str]:
        """Wait until WeaselServer remains present across several probes.

        WeaselDeployer returning zero only proves that the deployer process
        finished.  During a real deployment the input service may still be
        restarting, so reporting success immediately can let another capture
        start a second deployment while Weasel is not ready yet.
        """
        if os.name != "nt":
            return True, "部署已完成。"
        server_path = Path(self._exe or "").with_name("WeaselServer.exe")
        if not server_path.exists():
            # Keep compatibility with non-standard Weasel layouts.  There is
            # no reliable process name to probe in that case, but a short
            # settle period still avoids an immediate consecutive deployment.
            time.sleep(2.0)
            return True, "部署已完成；未找到算法服务程序，无法自动确认其状态。"

        deadline = time.monotonic() + _SERVER_READY_TIMEOUT_SECONDS
        stable_polls = 0
        while time.monotonic() < deadline:
            running = self._weasel_server_running()
            if running is None:
                return False, f"部署器已完成，但无法确认算法服务状态；{_FALLBACK_HINT}"
            if running:
                stable_polls += 1
                if stable_polls >= _SERVER_STABLE_POLLS:
                    return True, "部署完成，算法服务已恢复。"
            else:
                stable_polls = 0
            time.sleep(_SERVER_READY_POLL_SECONDS)
        return False, f"部署器已完成，但算法服务未在限定时间内恢复；{_FALLBACK_HINT}"

    @staticmethod
    def _weasel_server_running() -> Optional[bool]:
        """Return WeaselServer process state, or ``None`` when probing fails."""
        try:
            result = subprocess.run(
                [
                    "tasklist", "/FI", "IMAGENAME eq WeaselServer.exe",
                    "/FO", "CSV", "/NH",
                ],
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=4,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return "weaselserver.exe" in (result.stdout or "").lower()

    # ------------------------------------------------------------------ #
    # 探测部署器路径
    # ------------------------------------------------------------------ #
    def _detect(self) -> Optional[str]:
        # 1) 用户手动指定优先
        manual = getattr(self._settings, "deployer_path", "") if self._settings else ""
        manual = (manual or "").strip()
        if manual and Path(manual).exists():
            logger.info("使用手动指定的部署器：%s", manual)
            return str(manual)

        # 2) 注册表 WeaselRoot
        try:
            import winreg  # type: ignore

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for subkey in (r"Software\Rime\Weasel", r"Software\Rime"):
                    try:
                        with winreg.OpenKey(root, subkey) as key:
                            val, _ = winreg.QueryValueEx(key, "WeaselRoot")
                        cand = Path(val) / "WeaselDeployer.exe"
                        if cand.exists():
                            return str(cand)
                    except OSError:
                        continue
        except Exception:
            pass

        # 3) 扩展递归搜索
        exe = self._search_exe()
        if exe:
            return exe
        return None

    def _search_exe(self) -> Optional[str]:
        """Probe bounded known layouts; never recursively scan user profiles."""
        candidates: List[Path] = []
        for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(env_key)
            if not value:
                continue
            root = Path(value)
            candidates.extend((
                root / "Rime" / "WeaselDeployer.exe",
                root / "Programs" / "Rime" / "WeaselDeployer.exe",
            ))
            try:
                candidates.extend(root.glob("Rime/weasel-*/WeaselDeployer.exe"))
                candidates.extend(root.glob("Programs/Rime/weasel-*/WeaselDeployer.exe"))
            except OSError:
                pass
        for drive in ("C:\\", "D:\\", "E:\\"):
            for base in ("Program Files", "Program Files (x86)"):
                root = Path(drive) / base / "Rime"
                candidates.append(root / "WeaselDeployer.exe")
                try:
                    candidates.extend(root.glob("weasel-*/WeaselDeployer.exe"))
                except OSError:
                    pass
        seen: set[str] = set()
        for cand in candidates:
            key = str(cand).casefold()
            if key in seen:
                continue
            seen.add(key)
            if cand.is_file():
                logger.info("自动探测到部署器：%s", cand)
                return str(cand)
        return None
