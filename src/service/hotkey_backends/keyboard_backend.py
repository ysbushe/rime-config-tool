"""热键后端：keyboard 库实现（默认）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes  # 显式导入子模块，否则打包后 ctypes.wintypes 不可用
import os
import time
from typing import Callable, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

# WM_COPY：向焦点控件发送后，标准 edit/richedit/combo 控件会复制选中内容到剪贴板
WM_COPY = 0x0301

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002
ASFW_ANY = 0xFFFF
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_UNICODE = 0x0004
MAPVK_VK_TO_VSC = 0

user32 = ctypes.windll.user32


def _is_internal_workdir_text(text: str) -> bool:
    value = (text or "").strip().strip('"')
    if not value or "\n" in value or "\r" in value:
        return False
    return os.path.normcase(os.path.abspath(value)) == os.path.normcase(os.getcwd())


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcWindow", ctypes.wintypes.RECT),
    ]


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("_i",)
    _fields_ = [("type", ctypes.wintypes.DWORD), ("_i", _INPUTUNION)]

class KeyboardBackend:
    """基于 keyboard 库的全局热键后端。"""

    def __init__(self) -> None:
        self._kb = None
        self._combo: Optional[str] = None
        try:
            import keyboard  # type: ignore

            self._kb = keyboard
            self._available = True
        except Exception as exc:
            logger.warning("keyboard 后端不可用：%s", exc)
            self._available = False

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "keyboard"

    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------ #
    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        if not self._available:
            return False
        try:
            # keyboard 使用小写加号组合，如 "ctrl+alt+q"
            normalized = combo.lower().replace(" ", "")
            self._kb.add_hotkey(normalized, callback)
            self._combo = normalized
            logger.info("已注册热键（keyboard）：%s", combo)
            return True
        except Exception as exc:
            logger.warning("注册热键失败：%s", exc)
            return False

    def unregister(self) -> None:
        if self._available and self._combo:
            try:
                self._kb.remove_hotkey(self._combo)
            except Exception:
                pass
            self._combo = None

    # ------------------------------------------------------------------ #
    def capture_target(self) -> tuple[int, int, int]:
        """在热键触发瞬间记录前台窗口、线程和焦点控件。"""
        fg_hwnd = 0
        fg_thread = 0
        focus_hwnd = 0
        try:
            fg_hwnd = int(user32.GetForegroundWindow())
            fg_thread = int(user32.GetWindowThreadProcessId(fg_hwnd, None))
            gui = GUITHREADINFO()
            gui.cbSize = ctypes.sizeof(GUITHREADINFO)
            if user32.GetGUIThreadInfo(fg_thread, ctypes.byref(gui)):
                focus_hwnd = int(gui.hwndFocus) or int(gui.hwndActive) or fg_hwnd
        except Exception:
            focus_hwnd = focus_hwnd or fg_hwnd
        return fg_hwnd, fg_thread, focus_hwnd

    def capture_selection(self, target: tuple[int, int, int] | None = None) -> str:
        """采集当前前台窗口的选中文本，采集结束后恢复剪贴板。

        与 win32 后端对称：用 ``GetGUIThreadInfo`` 取前台线程真实焦点控件、
        检查 ``AttachThreadInput`` 返回值、best-effort 前台化，先发 ``WM_COPY``，
        未变化时再回退 keyboard 库的 Ctrl+C 模拟按键（keyboard 库 send 较可靠，
        保留；先判 ``self._kb is not None``）。
        """
        saved = self._get_clipboard()
        sentinel = f"__RIME_CONFIG_TOOL_CAPTURE_SENTINEL_{time.monotonic_ns()}__"
        self._set_clipboard(sentinel)
        probe = self._get_clipboard()
        fg_hwnd, fg_thread, focus_hwnd = target or self.capture_target()
        cur_thread = 0
        text = ""
        ctrl_c_sent = False
        attached = False
        try:
            try:
                cur_thread = int(user32.GetCurrentThreadId())
            except Exception:
                pass

            # c. 附加线程输入（检查返回值，失败仅降级，不静默致命）
            if fg_thread and fg_thread != cur_thread:
                try:
                    if user32.AttachThreadInput(cur_thread, fg_thread, True) != 0:
                        attached = True
                    else:
                        logger.warning("AttachThreadInput 失败，降级走前台化+Ctrl+C 兜底")
                except Exception:
                    pass

            # d. 发复制前 best-effort 前台化，确保按键落点正确
            try:
                user32.AllowSetForegroundWindow(ASFW_ANY)
                if fg_hwnd:
                    user32.SetForegroundWindow(int(fg_hwnd))
                if focus_hwnd:
                    user32.SetFocus(int(focus_hwnd))
            except Exception:
                pass

            # e. 先发 WM_COPY（标准 edit/richedit/combo 收到即复制选中内容）
            try:
                if focus_hwnd:
                    user32.PostMessageW(int(focus_hwnd), WM_COPY, 0, 0)
            except Exception:
                pass

            # f. 给 WM_COPY 一个短响应窗口，未变化再发一次 Ctrl+C 兜底。
            deadline = time.time() + 0.45
            wm_copy_grace = time.time() + 0.05
            capture_source = "wm_copy"
            while time.time() < deadline:
                cur = self._get_clipboard()
                changed = (
                    (probe == sentinel and cur != sentinel)
                    or (probe != sentinel and cur != probe)
                )
                if cur and cur != saved and changed:
                    if _is_internal_workdir_text(cur):
                        logger.warning(
                            "热键取词：忽略程序工作目录 source=%s",
                            capture_source,
                        )
                    else:
                        text = cur
                        logger.info("热键取词：捕获来源=%s", capture_source)
                        break
                if not ctrl_c_sent and time.time() >= wm_copy_grace:
                    ctrl_c_sent = True
                    self._wait_for_hotkey_release(timeout=0.12)
                    capture_source = "ctrl+c"
                    self._send_ctrl_c()
                time.sleep(0.02)

            text = text or ""
        finally:
            if attached:
                try:
                    user32.AttachThreadInput(cur_thread, fg_thread, False)
                except Exception:
                    pass
            time.sleep(0.02)
            self._set_clipboard(saved)
        return text

    @staticmethod
    def _wait_for_hotkey_release(timeout: float = 0.12) -> None:
        """短等热键修饰键松开，避免 Ctrl+Alt+Q 触发后兜底复制变成 Ctrl+Alt+C。"""
        deadline = time.time() + timeout
        keys = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)
        while time.time() < deadline:
            try:
                if all((int(user32.GetAsyncKeyState(vk)) & 0x8000) == 0 for vk in keys):
                    return
            except Exception:
                return
            time.sleep(0.01)
    def _send_ctrl_c(self) -> None:
        """Ctrl+C 兜底：通过 keyboard 库模拟按键复制。"""
        try:
            if self._kb is not None:
                for key in ("alt", "shift", "windows"):
                    try:
                        self._kb.release(key)
                    except Exception:
                        pass
                self._kb.send("ctrl+c")
        except Exception:
            pass

    # —— 剪贴板辅助（win32clipboard） —— #
    @staticmethod
    def _get_clipboard() -> str:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                    raw = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                    data = raw.decode("latin-1") if isinstance(raw, bytes) else raw
                else:
                    data = ""
            finally:
                win32clipboard.CloseClipboard()
            return data or ""
        except Exception:
            return ""

    @staticmethod
    def _set_clipboard(text: str) -> None:
        try:
            import win32clipboard  # type: ignore

            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text or "")
            win32clipboard.CloseClipboard()
        except Exception:
            pass
