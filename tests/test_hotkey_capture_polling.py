"""热键采集（Bug B）独立回归测试。

验证 capture_selection() 的轮询逻辑真实生效：
- 源程序在短轮询窗口内更新剪贴板（≠ saved）时能被捕获；
- 触发路径使用 WM_COPY + 线程输入附加（AttachThreadInput），并保留 Ctrl+C 兜底；
- 源程序复制慢于 0.45s（已知限制）时超时回退空串；
- 采集结束后原剪贴板被恢复。

全程 monkeypatch 剪贴板与按键 / win32 API，不触碰真实系统剪贴板 / 不发送真实按键。
"""
from __future__ import annotations

import ctypes
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.service.hotkey_backends import win32_backend
from src.service.hotkey_backends import keyboard_backend


def _install_fake_win32gui(monkeypatch):
    """注入无副作用的 win32gui，使 SetForegroundWindow 成为 no-op。"""
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    return fake


def _patch_clipboard(backend, getter_factory, recorded):
    """用可变的 getter 覆盖 _get_clipboard，并记录 _set_clipboard 调用。"""
    backend._get_clipboard = getter_factory
    backend._set_clipboard = lambda text: recorded.append(text)

def _assert_sentinel(text: str) -> None:
    assert text.startswith("__RIME_CONFIG_TOOL_CAPTURE_SENTINEL_")



def test_sendinput_struct_layout_matches_windows():
    """SendInput requires the full Windows INPUT layout; a short struct makes Ctrl+C a no-op."""
    ptr_size = ctypes.sizeof(ctypes.c_void_p)
    expected_input = 40 if ptr_size == 8 else 28
    expected_keybd = 24 if ptr_size == 8 else 16
    expected_mouse = 32 if ptr_size == 8 else 24

    assert ctypes.sizeof(win32_backend.INPUT) == expected_input
    assert ctypes.sizeof(win32_backend.KEYBDINPUT) == expected_keybd
    assert ctypes.sizeof(win32_backend.MOUSEINPUT) == expected_mouse
    assert ctypes.sizeof(keyboard_backend.INPUT) == expected_input



def test_win32_send_ctrl_c_falls_back_to_keybd_event(monkeypatch):
    """SendInput may return 0 on some desktops; keybd_event must still send Ctrl+C."""
    fake_user32 = MagicMock()
    fake_user32.SendInput.return_value = 0
    monkeypatch.setattr(win32_backend, "user32", fake_user32)

    win32_backend.Win32HotkeyBackend._send_ctrl_c()

    assert fake_user32.SendInput.called
    assert fake_user32.keybd_event.call_count >= 4


def test_win32_copy_attempt_order_by_window_class():
    assert win32_backend.Win32HotkeyBackend._copy_attempts_for(
        "Chrome_WidgetWin_1", "Chrome_WidgetWin_1"
    ) == ("ctrl+c", "ctrl+shift+c", "ctrl+insert")
    assert win32_backend.Win32HotkeyBackend._copy_attempts_for(
        "CASCADIA_HOSTING_WINDOW_CLASS", ""
    ) == ("ctrl+shift+c", "ctrl+insert")
    assert win32_backend.Win32HotkeyBackend._copy_attempts_for(
        "", "ConsoleWindowClass"
    ) == ("ctrl+shift+c", "ctrl+insert")


def test_win32_terminal_capture_skips_ctrl_c(win32_backend_patched, monkeypatch):
    b = win32_backend_patched
    win32_backend.user32.GetForegroundWindow.return_value = 0x100
    win32_backend.user32.GetWindowThreadProcessId.return_value = 0x200
    win32_backend.user32.GetCurrentThreadId.return_value = 0x300
    win32_backend.user32.GetGUIThreadInfo.return_value = True

    state = {"text": "previously-on-clipboard"}
    recorded = []
    attempts = []

    def getter():
        return state["text"]

    def setter(text):
        recorded.append(text)
        state["text"] = text

    def fake_class_name(_hwnd):
        return "CASCADIA_HOSTING_WINDOW_CLASS"

    def fake_send_copy(name):
        attempts.append(name)
        if name == "ctrl+shift+c":
            state["text"] = "terminal copied text"

    b._get_clipboard = getter
    b._set_clipboard = setter
    monkeypatch.setattr(win32_backend, "_hwnd_class_name", fake_class_name)
    monkeypatch.setattr(
        win32_backend.Win32HotkeyBackend,
        "_send_copy_shortcut",
        staticmethod(fake_send_copy),
    )
    monkeypatch.setattr(
        win32_backend.Win32HotkeyBackend,
        "_wait_for_hotkey_release",
        staticmethod(lambda *_args, **_kwargs: None),
    )

    result = b.capture_selection()

    assert result == "terminal copied text"
    assert attempts and attempts[0] == "ctrl+shift+c"
    assert "ctrl+c" not in attempts
    _assert_sentinel(recorded[0])
    assert recorded[-1] == "previously-on-clipboard"

def test_win32_capture_rejects_republished_saved_clipboard(win32_backend_patched):
    """无选区时应用重新发布旧剪贴板，必须按未捕获处理。"""
    b = win32_backend_patched
    win32_backend.user32.GetForegroundWindow.return_value = 0x100
    win32_backend.user32.GetWindowThreadProcessId.return_value = 0x200
    win32_backend.user32.GetCurrentThreadId.return_value = 0x300
    win32_backend.user32.GetGUIThreadInfo.return_value = True

    selected = "G:/VSC/rime-config-tool"
    state = {"text": selected}
    recorded = []

    def getter():
        return state["text"]

    def setter(text):
        recorded.append(text)
        state["text"] = text

    def copy_selected(*_args):
        state["text"] = selected

    b._get_clipboard = getter
    b._set_clipboard = setter
    win32_backend.user32.PostMessageW.side_effect = copy_selected

    result = b.capture_selection()

    assert result == ""
    _assert_sentinel(recorded[0])
    assert recorded[-1] == selected

def _delayed_getter(saved, new, delay):
    """返回一个 callable：前 delay 秒返回 saved，之后返回 new（模拟源复制慢）。"""
    t0 = time.time()

    def getter():
        return new if (time.time() - t0) >= delay else saved

    return getter


# --------------------------------------------------------------------------- #
# Win32 后端
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win32_backend_patched(monkeypatch):
    _install_fake_win32gui(monkeypatch)
    b = win32_backend.Win32HotkeyBackend()
    # 中和真实按键发送
    monkeypatch.setattr(win32_backend, "user32", MagicMock())
    return b


def test_win32_capture_catches_clipboard_change(win32_backend_patched, monkeypatch):
    b = win32_backend_patched
    recorded = []
    saved = "previously-on-clipboard"
    getter = _delayed_getter(saved, "选中文本ABC", delay=0.15)  # 源 150ms 后复制完成
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert result == "选中文本ABC", f"应在轮询窗口内捕获剪贴板变化，实际得到 {result!r}"
    assert recorded and recorded[-1] == saved, "采集后应恢复原始剪贴板"


def test_win32_capture_uses_wm_copy_and_thread_attach(win32_backend_patched):
    """新路径验证（Bug B 修复核心）：WM_COPY + AttachThreadInput + SendInput 兜底。"""
    b = win32_backend_patched
    # 让 user32 各调用返回可控值，确保 AttachThreadInput / PostMessageW / SendInput 路径被触发
    win32_backend.user32.GetForegroundWindow.return_value = 0x100
    win32_backend.user32.GetWindowThreadProcessId.return_value = 0x200
    win32_backend.user32.GetCurrentThreadId.return_value = 0x300
    win32_backend.user32.GetFocus.return_value = 0x400
    # GetGUIThreadInfo 返回 True 走通 if 分支（gui 字段为 0 -> 降级取 fg_hwnd）
    win32_backend.user32.GetGUIThreadInfo.return_value = True

    recorded = []
    saved = "previously-on-clipboard"
    # delay=0.3：先于 0.15s 触发 SendInput 兜底，再于 0.3s 前捕获变化
    getter = _delayed_getter(saved, "WM_COPY采集到的文本", delay=0.3)
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert win32_backend.user32.PostMessageW.called, "应已向焦点控件发送 WM_COPY"
    assert win32_backend.user32.AttachThreadInput.called, "应已执行线程输入附加"
    assert win32_backend.user32.GetGUIThreadInfo.called, "应已调用 GetGUIThreadInfo 取焦点控件"
    assert win32_backend.user32.SendInput.called, "WM_COPY 未命中时应已用 SendInput 兜底 Ctrl+C"
    assert result == "WM_COPY采集到的文本", f"应在轮询窗口内捕获剪贴板变化，实际得到 {result!r}"
    assert recorded and recorded[-1] == saved, "采集后应恢复原始剪贴板"


def test_win32_capture_times_out_when_source_slow(win32_backend_patched, monkeypatch):
    b = win32_backend_patched
    recorded = []
    saved = "previously-on-clipboard"
    # 源复制需 2s，远超 0.45s 轮询上限 -> 已知限制：超时回退空串
    getter = _delayed_getter(saved, "太晚了", delay=2.0)
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert result == "", "源慢于 0.45s 时应超时回退空串（已知限制，非 bug）"
    assert recorded and recorded[-1] == saved, "即使超时也应恢复原始剪贴板"


# --------------------------------------------------------------------------- #
# keyboard 后端
# --------------------------------------------------------------------------- #
@pytest.fixture()
def keyboard_backend_patched(monkeypatch):
    b = keyboard_backend.KeyboardBackend()
    b._kb = MagicMock()  # 中和真实 ctrl+c 发送
    b._available = True
    return b


def test_keyboard_capture_rejects_republished_saved_clipboard(
    keyboard_backend_patched, monkeypatch
):
    b = keyboard_backend_patched
    monkeypatch.setattr(keyboard_backend, "user32", MagicMock())
    saved = "G:/VSC/rime-config-tool"
    state = {"text": saved}
    recorded = []

    def getter():
        return state["text"]

    def setter(text):
        recorded.append(text)
        state["text"] = text

    def republish_saved():
        state["text"] = saved

    b._get_clipboard = getter
    b._set_clipboard = setter
    b._send_ctrl_c = republish_saved

    result = b.capture_selection()

    assert result == ""
    _assert_sentinel(recorded[0])
    assert recorded[-1] == saved


def test_keyboard_capture_catches_clipboard_change(keyboard_backend_patched, monkeypatch):
    b = keyboard_backend_patched
    recorded = []
    saved = "previously-on-clipboard"
    getter = _delayed_getter(saved, "选中文本XYZ", delay=0.2)  # 源 200ms 后复制完成
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert result == "选中文本XYZ", f"应在轮询窗口内捕获剪贴板变化，实际得到 {result!r}"
    assert recorded and recorded[-1] == saved, "采集后应恢复原始剪贴板"


def test_keyboard_capture_uses_wm_copy_and_thread_attach(keyboard_backend_patched, monkeypatch):
    """新路径验证（Bug B 修复核心）：WM_COPY + AttachThreadInput + ctrl+c 兜底。"""
    b = keyboard_backend_patched
    # 中和真实 user32，并让各调用返回可控值以触发新路径
    monkeypatch.setattr(keyboard_backend, "user32", MagicMock())
    keyboard_backend.user32.GetForegroundWindow.return_value = 0x100
    keyboard_backend.user32.GetWindowThreadProcessId.return_value = 0x200
    keyboard_backend.user32.GetCurrentThreadId.return_value = 0x300
    keyboard_backend.user32.GetFocus.return_value = 0x400
    keyboard_backend.user32.GetGUIThreadInfo.return_value = True

    recorded = []
    saved = "previously-on-clipboard"
    # delay=0.3：先于 0.15s 触发 ctrl+c 兜底，再于 0.3s 前捕获变化
    getter = _delayed_getter(saved, "WM_COPY采集到的文本K", delay=0.3)
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert keyboard_backend.user32.PostMessageW.called, "应已向焦点控件发送 WM_COPY"
    assert keyboard_backend.user32.AttachThreadInput.called, "应已执行线程输入附加"
    assert keyboard_backend.user32.GetGUIThreadInfo.called, "应已调用 GetGUIThreadInfo 取焦点控件"
    assert b._kb.send.called, "WM_COPY 未命中时应已用 keyboard.send 兜底 Ctrl+C"
    assert result == "WM_COPY采集到的文本K", f"应在轮询窗口内捕获剪贴板变化，实际得到 {result!r}"
    assert recorded and recorded[-1] == saved, "采集后应恢复原始剪贴板"


def test_keyboard_capture_times_out_when_source_slow(keyboard_backend_patched, monkeypatch):
    b = keyboard_backend_patched
    recorded = []
    saved = "previously-on-clipboard"
    getter = _delayed_getter(saved, "太晚了", delay=2.0)
    _patch_clipboard(b, getter, recorded)

    result = b.capture_selection()

    assert result == "", "源慢于 0.45s 时应超时回退空串（已知限制，非 bug）"
    assert recorded and recorded[-1] == saved, "即使超时也应恢复原始剪贴板"
