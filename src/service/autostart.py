"""开机自启（Autostart）。

在 Windows 启动目录创建 / 移除指向本程序的 .lnk 快捷方式。
无显示环境 / 缺 pywin32 时优雅降级（无法创建则记录并提示）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_LINK_NAME = "RimeConfig.lnk"


class Autostart:
    """管理开机自启快捷方式。"""

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            self._startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        else:
            self._startup = Path.home() / "Startup"
        self._link = self._startup / _LINK_NAME

    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return self._link.exists()

    def enable(self, target: Optional[str] = None) -> bool:
        """创建自启快捷方式。target 默认取当前可执行文件。"""
        target = target or self._current_exe()
        if not target or not Path(target).exists():
            logger.warning("自启目标不存在，跳过：%s", target)
            return False
        try:
            self._startup.mkdir(parents=True, exist_ok=True)
            self._create_shortcut(target, str(self._link))
            logger.info("已启用开机自启：%s", self._link)
            return True
        except Exception as exc:
            logger.warning("启用开机自启失败：%s", exc)
            return False

    def disable(self) -> bool:
        try:
            if self._link.exists():
                self._link.unlink()
            logger.info("已禁用开机自启：%s", self._link)
            return True
        except Exception as exc:
            logger.warning("禁用开机自启失败：%s", exc)
            return False

    # ------------------------------------------------------------------ #
    @staticmethod
    def _current_exe() -> Optional[str]:
        """当前可执行文件路径（PyInstaller 单文件下为 sys.executable）。"""
        exe = sys.executable
        return exe if exe and exe.lower().endswith(".exe") else None

    @staticmethod
    def _create_shortcut(target: str, link_path: str) -> None:
        try:
            import win32com.client  # type: ignore

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(link_path)
            shortcut.TargetPath = target
            shortcut.WorkingDirectory = os.path.dirname(target)
            shortcut.Description = "RIME 配置小工具"
            shortcut.save()
        except Exception:
            # 无 pywin32 时退化为 .bat（功能等价，仅非原生 .lnk）
            bat_path = str(Path(link_path).with_suffix(".bat"))
            Path(bat_path).write_text(
                f'@echo off\r\nstart "" "{target}"\r\n', encoding="utf-8"
            )
            logger.info("pywin32 不可用，已用 .bat 降级实现自启：%s", bat_path)
