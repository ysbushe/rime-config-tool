"""热键后端：AutoHotkey v2 实现（降级）。

当 keyboard 后端不可用时使用。原理：
    1. 生成一份 AHK v2 脚本，监听热键并模拟 Ctrl+C 抓取选中文本
    2. 将抓取到的文本写入信号文件 %TEMP%/rime_hotkey_signal.txt
    3. 后端轮询信号文件，变化时读取内容并回调

无需管理员权限，跨场景更稳，但要求本机已安装 AutoHotkey v2。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

_SIGNAL_FILE = Path(os.environ.get("TEMP", "/tmp")) / "rime_hotkey_signal.txt"


class AhkBackend:
    """基于 AutoHotkey v2 的全局热键后端（降级方案）。"""

    def __init__(self) -> None:
        self._exe = self._detect_ahk()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._callback: Optional[Callable[[str], None]] = None
        self._combo_ahk: Optional[str] = None
        self._last_mtime = 0.0

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "ahk"

    def available(self) -> bool:
        return self._exe is not None

    # ------------------------------------------------------------------ #
    def register(self, combo: str, callback: Callable[[str], None]) -> bool:
        if not self._exe:
            return False
        self._callback = callback
        self._combo_ahk = self._to_ahk(combo)
        script = self._build_script(self._combo_ahk)
        script_path = Path(os.environ.get("TEMP", "/tmp")) / "rime_hotkey.ahk"
        script_path.write_text(script, encoding="utf-8")
        try:
            self._proc = subprocess.Popen([self._exe, str(script_path)])
            self._stop.clear()
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()
            logger.info("已注册热键（AHK v2）：%s", combo)
            return True
        except Exception as exc:
            logger.warning("启动 AHK 后端失败：%s", exc)
            return False

    def unregister(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._thread = None

    # ------------------------------------------------------------------ #
    # 轮询信号文件
    # ------------------------------------------------------------------ #
    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                if _SIGNAL_FILE.exists():
                    mtime = _SIGNAL_FILE.stat().st_mtime
                    if mtime != self._last_mtime:
                        self._last_mtime = mtime
                        text = _SIGNAL_FILE.read_text(encoding="utf-8", errors="ignore").strip()
                        if text and self._callback:
                            self._callback(text)
            except Exception:
                pass
            time.sleep(0.2)

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_ahk(combo: str) -> str:
        """Ctrl+Alt+Q -> ^!q。"""
        parts = [p.strip().lower() for p in combo.replace(" ", "").split("+") if p.strip()]
        table = {"ctrl": "^", "control": "^", "alt": "!", "shift": "+", "win": "#"}
        out = ""
        key = ""
        for p in parts:
            if p in table:
                out += table[p]
            else:
                key = p
        return f"{out}{key}"

    @staticmethod
    def _build_script(combo_ahk: str) -> str:
        return (
            "#SingleInstance Force\n"
            "#Persistent\n"
            f"{combo_ahk}::\n"
            "{\n"
            "    Send \"^c\"\n"
            "    Sleep 120\n"
            f"    FileAppend A_Clipboard \"`n\", \"{str(_SIGNAL_FILE).replace(chr(92), '/')}\"\n"
            "}\n"
            "Return\n"
        )

    @staticmethod
    def _detect_ahk() -> Optional[str]:
        # 1) PATH
        for name in ("AutoHotkey.exe", "AutoHotkeyUX.exe"):
            from shutil import which

            found = which(name)
            if found:
                return found
        # 2) 注册表
        try:
            import winreg  # type: ignore

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for subkey in (r"Software\AutoHotkey", r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AutoHotkey"):
                    try:
                        with winreg.OpenKey(root, subkey) as key:
                            val, _ = winreg.QueryValueEx(key, "InstallDir")
                        cand = Path(val) / "AutoHotkey.exe"
                        if cand.exists():
                            return str(cand)
                    except OSError:
                        continue
        except Exception:
            pass
        return None
