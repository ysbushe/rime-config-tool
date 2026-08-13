"""Read a selection from a standard Windows Edit control without key injection."""
from __future__ import annotations

import ctypes
import ctypes.wintypes


EM_GETSEL = 0x00B0
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
_MESSAGE_TIMEOUT_MS = 80
_MAX_EDIT_TEXT_LENGTH = 2_000_000

user32 = ctypes.windll.user32


def _send_message_timeout(hwnd: int, message: int, wparam: int = 0, lparam: int = 0) -> tuple[bool, int]:
    result = ctypes.c_size_t()
    try:
        ok = user32.SendMessageTimeoutW(
            ctypes.wintypes.HWND(int(hwnd)),
            ctypes.wintypes.UINT(message),
            ctypes.wintypes.WPARAM(wparam),
            ctypes.wintypes.LPARAM(lparam),
            ctypes.wintypes.UINT(SMTO_BLOCK | SMTO_ABORTIFHUNG),
            ctypes.wintypes.UINT(_MESSAGE_TIMEOUT_MS),
            ctypes.byref(result),
        )
    except Exception:
        return False, 0
    return bool(ok), int(result.value)


def _class_name(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(int(hwnd), buffer, len(buffer))
        return buffer.value
    except Exception:
        return ""


def read_standard_edit_selection(hwnd: int) -> str:
    """Return selected text from a standard ``Edit`` control, or an empty string."""
    if not hwnd or _class_name(hwnd).casefold() != "edit":
        return ""

    start = ctypes.wintypes.DWORD()
    end = ctypes.wintypes.DWORD()
    ok, _ = _send_message_timeout(
        hwnd,
        EM_GETSEL,
        ctypes.addressof(start),
        ctypes.addressof(end),
    )
    if not ok or end.value <= start.value:
        return ""

    ok, length = _send_message_timeout(hwnd, WM_GETTEXTLENGTH)
    if not ok or length <= 0 or length > _MAX_EDIT_TEXT_LENGTH or end.value > length:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    ok, copied = _send_message_timeout(
        hwnd,
        WM_GETTEXT,
        length + 1,
        ctypes.addressof(buffer),
    )
    if not ok or copied <= 0:
        return ""
    return buffer.value[start.value:end.value]
