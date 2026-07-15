"""Windows autostart shortcut management."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_LINK_NAME = "RimeConfig.lnk"
_AUTOSTART_ARGUMENT = "--autostart"


class Autostart:
    """Manage the Startup-folder shortcut for this application."""

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            self._startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        else:
            self._startup = Path.home() / "Startup"
        self._link = self._startup / _LINK_NAME

    @property
    def enabled(self) -> bool:
        return self._link.exists() or self._link.with_suffix(".bat").exists()

    def enable(self, target: Optional[str] = None) -> bool:
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
            for path in (self._link, self._link.with_suffix(".bat")):
                if path.exists():
                    path.unlink()
            logger.info("已禁用开机自启：%s", self._link)
            return True
        except Exception as exc:
            logger.warning("禁用开机自启失败：%s", exc)
            return False

    @staticmethod
    def _current_exe() -> Optional[str]:
        exe = sys.executable
        return exe if exe and exe.lower().endswith(".exe") else None

    @staticmethod
    def _create_shortcut(target: str, link_path: str) -> None:
        try:
            import win32com.client  # type: ignore

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(link_path)
            shortcut.TargetPath = target
            shortcut.Arguments = _AUTOSTART_ARGUMENT
            shortcut.WorkingDirectory = os.path.dirname(target)
            shortcut.Description = "RIME 配置小工具"
            shortcut.save()
        except Exception:
            bat_path = Path(link_path).with_suffix(".bat")
            bat_path.write_text(
                f'@echo off\r\nstart "" "{target}" {_AUTOSTART_ARGUMENT}\r\n', encoding="utf-8"
            )
            logger.info("pywin32 不可用，已用 .bat 降级实现自启：%s", bat_path)
