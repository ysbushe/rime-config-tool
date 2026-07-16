"""Windows autostart shortcut management with target validation."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_LINK_NAME = "RimeConfig.lnk"
_AUTOSTART_ARGUMENT = "--autostart"


@dataclass(frozen=True)
class AutostartStatus:
    """The state of this application's managed startup entry."""

    enabled: bool
    reason: str
    target: str = ""


class Autostart:
    """Manage and validate the Startup-folder entry for this application."""

    def __init__(self) -> None:
        appdata = os.environ.get("APPDATA")
        if appdata:
            self._startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        else:
            self._startup = Path.home() / "Startup"
        self._link = self._startup / _LINK_NAME

    @property
    def enabled(self) -> bool:
        return self.status().enabled

    def status(self, target: Optional[str] = None) -> AutostartStatus:
        """Return whether the managed entry launches the current executable correctly."""
        expected = self._normalize_target(target or self._current_exe())
        entries = (self._link, self._link.with_suffix(".bat"))
        present = [entry for entry in entries if entry.is_file()]
        if not present:
            return AutostartStatus(False, "未启用")
        if not expected:
            return AutostartStatus(False, "当前不是发布版，无法验证开机自启")

        stale_target = ""
        for entry in present:
            actual, arguments = self._read_entry(entry)
            normalized = self._normalize_target(actual)
            if not normalized:
                continue
            if normalized != expected:
                stale_target = actual
                continue
            if _AUTOSTART_ARGUMENT not in arguments.split():
                return AutostartStatus(False, "自启项缺少最小化启动参数", actual)
            return AutostartStatus(True, "已正确启用", actual)

        if stale_target:
            return AutostartStatus(False, "自启项指向旧程序位置", stale_target)
        return AutostartStatus(False, "自启项无法读取或已损坏")

    def enable(self, target: Optional[str] = None) -> bool:
        target = target or self._current_exe()
        if not target or not Path(target).is_file():
            logger.warning("自启目标不存在，跳过：%s", target)
            return False
        try:
            self._startup.mkdir(parents=True, exist_ok=True)
            self.disable()
            self._create_shortcut(target, str(self._link))
            ok = self.status(target).enabled
            logger.info("启用开机自启：%s result=%s", self._link, ok)
            return ok
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

    def _read_entry(self, path: Path) -> tuple[str, str]:
        if path.suffix.lower() == ".bat":
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return "", ""
            match = re.search(r'start\s+""\s+"([^"]+)"(?:\s+(.+))?', text, re.IGNORECASE)
            return (match.group(1), match.group(2) or "") if match else ("", "")
        try:
            import win32com.client  # type: ignore

            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(path))
            return str(shortcut.TargetPath or ""), str(shortcut.Arguments or "")
        except Exception as exc:
            logger.info("读取自启快捷方式失败：%s", exc)
            return "", ""

    @staticmethod
    def _normalize_target(target: Optional[str]) -> str:
        if not target:
            return ""
        try:
            return str(Path(target).resolve()).casefold()
        except OSError:
            return str(Path(target).absolute()).casefold()

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
