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
from pathlib import Path
from typing import List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

_FALLBACK_HINT = "请右键小狼毫托盘 → 重新部署"


class DeployService:
    """触发 Rime 重新部署。"""

    def __init__(self, settings=None) -> None:
        self._settings = settings
        self._exe = self._detect()

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        return self._exe is not None

    @property
    def deployer_path(self) -> Optional[str]:
        return self._exe

    def redetect(self) -> None:
        """重新探测部署器路径（如设置页『重新探测』触发）。"""
        self._exe = self._detect()

    # ------------------------------------------------------------------ #
    def deploy(self) -> Tuple[bool, str]:
        """触发部署。返回 (成功, 提示信息)。"""
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
            return True, "部署已触发，稍候生效。"
        except subprocess.TimeoutExpired:
            return False, f"部署超时，{_FALLBACK_HINT}"
        except Exception as exc:
            msg = f"部署失败：{exc}；{_FALLBACK_HINT}"
            logger.warning(msg)
            return False, msg

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
        """在合理的有限根目录内递归查找 WeaselDeployer.exe。"""
        roots: List[Path] = []
        for env_key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA",
                        "APPDATA", "ProgramData"):
            v = os.environ.get(env_key)
            if v:
                roots.append(Path(v))
        # Rime 目录父级（如 %APPDATA%\Rime → 搜 %APPDATA%）
        rd = getattr(self._settings, "rime_dir", "") if self._settings else ""
        if rd:
            rp = Path(rd)
            if rp.parent.exists():
                roots.append(rp.parent)
            if rp.exists():
                roots.append(rp)
        # 常见安装盘下的已知子目录（避免全盘扫描）
        for drive in ("C:\\", "D:\\", "E:\\"):
            if not Path(drive).exists():
                continue
            for sub in ("Program Files", "Program Files (x86)", "Rime"):
                p = Path(drive) / sub
                if p.exists():
                    roots.append(p)

        seen = set()
        for root in roots:
            key = str(root)
            if key in seen or not root.exists():
                continue
            seen.add(key)
            try:
                for cand in root.rglob("WeaselDeployer.exe"):
                    if cand.is_file():
                        logger.info("自动探测到部署器：%s", cand)
                        return str(cand)
            except Exception:
                continue
        return None
