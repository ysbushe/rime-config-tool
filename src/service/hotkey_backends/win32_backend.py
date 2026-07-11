"""热键后端：Win32 原生 RegisterHotKey（Windows 首选）。

相比 keyboard 钩子库：
    - 在系统层正式注册组合键，若被占用则注册失败 → 可确定性报告冲突；
    - 前台触发更稳定；因 Ctrl+Alt+Q 在系统层通常空闲，换此后可独占槽位、稳定触发；
    - 仍保留剪贴板抓取（模拟 Ctrl+C）以支撑「热键收藏」采集选中文本。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes  # 显式导入子模块，否则 ctypes.wintypes 不可用
import os
import sys
import time
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from src.utils.logger import get_logger

logger = get_logger(__name__)

_TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
VK_INSERT = 0x2D
KEYEVENTF_KEYUP = 0x0002
WM_COPY = 0x0301  # 向焦点控件发送后，标准 edit/richedit/combo 控件会复制选中内容到剪贴板

# SendInput / 前台化 / 焦点获取相关常量
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

def _hwnd_class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(int(hwnd), buf, len(buf))
        return buf.value
    except Exception:
        return ""


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
# 组合键 → 虚拟键码（键名统一小写，便于解析时小写匹配）
_VK: dict[str, int] = {chr(c).lower(): c for c in range(0x41, 0x5B)}      # a-z
for _c in range(0x30, 0x3A):
    _VK[chr(_c)] = _c                                               # 0-9
_VK.update({
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    "space": 0x20,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
})


class _HotkeyWindow(QWidget):
    """隐藏的接收窗口：捕获 WM_HOTKEY 消息并回调后端。"""

    def __init__(self, backend: "Win32HotkeyBackend") -> None:
        super().__init__()
        self._backend = backend
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # 强制创建原生句柄（无需显示即可接收 WM_HOTKEY）
        self.winId()

    def nativeEvent(self, eventType, message):  # type: ignore
        if bytes(eventType) == b"windows_generic_MSG":
            # PySide6 的 message 是 sip.voidptr，需转成整数地址再 cast
            msg = ctypes.cast(
                ctypes.c_void_p(int(message)),
                ctypes.POINTER(ctypes.wintypes.MSG),
            ).contents
            if msg.message == WM_HOTKEY and msg.wParam == self._backend._hotkey_id:
                self._backend._fire()
                return True, 0
        return super().nativeEvent(eventType, message)


class Win32HotkeyBackend:
    """基于 Win32 RegisterHotKey 的全局热键后端。"""

    def __init__(self) -> None:
        self._window: Optional[_HotkeyWindow] = None
        self._callback: Optional[Callable[[], None]] = None
        self._hotkey_id = 1
        self._available = sys.platform.startswith("win")

    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return "win32"

    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------ #
    def register(self, combo: str, callback: Callable[[], None]) -> bool:
        if not self._available:
            return False
        mod, vk = self._parse(combo)
        if vk is None:
            logger.warning("无法解析热键组合：%s", combo)
            return False
        if self._window is None:
            self._window = _HotkeyWindow(self)
        try:
            hwnd = int(self._window.winId())
            ok = user32.RegisterHotKey(hwnd, self._hotkey_id, mod, vk)
        except Exception as exc:
            logger.warning("RegisterHotKey 调用异常：%s 组合：%s", exc, combo)
            return False
        if not ok:
            err = ctypes.GetLastError()
            logger.warning("RegisterHotKey 失败（可能已被占用）错误码：%s 组合：%s",
                           err, combo)
            return False
        self._callback = callback
        logger.info("已注册热键（win32）：%s", combo)
        return True

    def unregister(self) -> None:
        if self._window is not None:
            try:
                user32.UnregisterHotKey(int(self._window.winId()), self._hotkey_id)
            except Exception:
                pass
        self._callback = None

    def _fire(self) -> None:
        if self._callback:
            self._callback()

    # ------------------------------------------------------------------ #
    # 剪贴板抓取（WM_COPY + 线程输入附加，供『热键收藏』采集选中文本）
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
        """采集当前前台窗口的选中文本，采集结束后恢复原始剪贴板。

        采用行业标准做法：

        * 用 ``GetGUIThreadInfo`` 取前台线程的真实焦点控件（比依赖
          ``AttachThreadInput`` 成功后的 ``GetFocus`` 更可靠，避免二次修复
          (Bug B) 中因 ``GetFocus`` 返回 0 导致 WM_COPY 根本没发出的问题）；
        * 检查 ``AttachThreadInput`` 返回值，失败仅降级，不再静默致命；
        * best-effort 前台化（``AllowSetForegroundWindow`` / ``SetForegroundWindow`` /
          ``SetFocus``），确保按键落点正确；
        * 先发 ``WM_COPY``，未变化时再回退 SendInput Ctrl+C 兜底；
        * 全程 try/except 兜底，采集结束恢复原始剪贴板。
        """
        saved = self._get_clipboard()
        sentinel = f"__RIME_CONFIG_TOOL_CAPTURE_SENTINEL_{time.monotonic_ns()}__"
        self._set_clipboard(sentinel)
        probe = self._get_clipboard()
        fg_hwnd, fg_thread, focus_hwnd = target or self.capture_target()
        cur_thread = 0
        text = ""
        attached = False
        try:
            try:
                cur_thread = int(user32.GetCurrentThreadId())
            except Exception:
                pass

            logger.info(
                "热键取词：前台窗口=%s(%s)，焦点控件=%s(%s)",
                fg_hwnd,
                _hwnd_class_name(fg_hwnd),
                focus_hwnd,
                _hwnd_class_name(focus_hwnd),
            )

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

            # f. 轮询：先等 WM_COPY 生效；未变化时按窗口类型依次尝试复制快捷键。
            #    普通程序偏向 Ctrl+C；终端类窗口优先 Ctrl+Shift+C，避免 Ctrl+C 中断。
            attempts = self._copy_attempts_for(_hwnd_class_name(fg_hwnd), _hwnd_class_name(focus_hwnd))
            attempt_index = 0
            deadline = time.time() + 0.45
            next_attempt_at = time.time() + 0.05
            release_waited = False
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
                if attempt_index < len(attempts) and time.time() >= next_attempt_at:
                    if not release_waited:
                        self._wait_for_hotkey_release(timeout=0.12)
                        release_waited = True
                    attempt = attempts[attempt_index]
                    capture_source = attempt
                    logger.info("热键取词：复制尝试=%s", attempt)
                    self._send_copy_shortcut(attempt)
                    attempt_index += 1
                    next_attempt_at = time.time() + 0.12
                time.sleep(0.02)

            text = text or ""
            logger.info("热键取词：结果=%s", "成功" if text else "空")
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
    def _copy_attempts_for(fg_class: str, focus_class: str) -> tuple[str, ...]:
        classes = {fg_class, focus_class}
        if classes & _TERMINAL_CLASSES:
            return ("ctrl+shift+c", "ctrl+insert")
        return ("ctrl+c", "ctrl+shift+c", "ctrl+insert")

    @staticmethod
    def _send_copy_shortcut(name: str) -> None:
        if name == "ctrl+c":
            Win32HotkeyBackend._send_ctrl_c()
        elif name == "ctrl+shift+c":
            Win32HotkeyBackend._send_keybd_shortcut((VK_CONTROL, VK_SHIFT), VK_C)
        elif name == "ctrl+insert":
            Win32HotkeyBackend._send_keybd_shortcut((VK_CONTROL,), VK_INSERT)

    @staticmethod
    def _send_keybd_shortcut(modifiers: tuple[int, ...], key: int) -> None:
        try:
            def _key(vk: int, flags: int = 0) -> None:
                user32.keybd_event(vk, 0, flags, 0)

            for vk in (VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN, VK_CONTROL):
                flags = KEYEVENTF_KEYUP
                if vk in (VK_LWIN, VK_RWIN):
                    flags |= KEYEVENTF_EXTENDEDKEY
                _key(vk, flags)
            time.sleep(0.02)
            for vk in modifiers:
                _key(vk, 0)
            _key(key, 0)
            _key(key, KEYEVENTF_KEYUP)
            for vk in reversed(modifiers):
                _key(vk, KEYEVENTF_KEYUP)
        except Exception:
            pass

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
    @staticmethod
    def _send_ctrl_c() -> None:
        """Ctrl+C 兜底：用 SendInput（比 keybd_event 更可靠的前台输入注入）。"""
        try:
            def _ki(vk, flags=0):
                k = KEYBDINPUT()
                k.wVk = vk
                k.wScan = 0
                k.dwFlags = flags
                k.time = 0
                k.dwExtraInfo = 0
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ki = k
                return inp

            inputs = [
                _ki(VK_MENU, KEYEVENTF_KEYUP),
                _ki(VK_SHIFT, KEYEVENTF_KEYUP),
                _ki(VK_LWIN, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
                _ki(VK_RWIN, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP),
                _ki(VK_CONTROL, KEYEVENTF_KEYUP),
                _ki(VK_CONTROL, 0),
                _ki(VK_C, 0),
                _ki(VK_C, KEYEVENTF_KEYUP),
                _ki(VK_CONTROL, KEYEVENTF_KEYUP),
            ]
            n = len(inputs)
            array = (INPUT * n)(*inputs)
            sent = user32.SendInput(n, array, ctypes.sizeof(INPUT))
            try:
                sent_count = int(sent)
            except Exception:
                sent_count = n
            if sent_count != n:
                logger.warning("SendInput Ctrl+C 未完整发送：%s/%s，降级 keybd_event", sent_count, n)
                Win32HotkeyBackend._send_ctrl_c_keybd_event()
        except Exception:
            Win32HotkeyBackend._send_ctrl_c_keybd_event()

    @staticmethod
    def _send_ctrl_c_keybd_event() -> None:
        """最终兜底：旧式 keybd_event 在部分桌面环境比 SendInput 更容易被接受。"""
        try:
            Win32HotkeyBackend._send_keybd_shortcut((VK_CONTROL,), VK_C)

        except Exception:
            pass

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

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse(combo: str):
        parts = [p.strip().lower() for p in combo.replace(" ", "").split("+")]
        mod = 0
        vk = None
        for p in parts:
            if p in ("ctrl", "control"):
                mod |= MOD_CONTROL
            elif p == "alt":
                mod |= MOD_ALT
            elif p == "shift":
                mod |= MOD_SHIFT
            elif p in ("win", "meta", "super"):
                mod |= MOD_WIN
            else:
                vk = _VK.get(p)
        return mod, vk
