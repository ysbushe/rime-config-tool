"""Windows notification identity setup with a graceful no-op on other platforms."""
from __future__ import annotations

import ctypes
import sys

APP_USER_MODEL_ID = "RimeConfigTool.Desktop"


def configure_windows_notification_identity() -> bool:
    """Give tray messages a stable Windows application identity when supported."""
    if not sys.platform.startswith("win"):
        return False
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        return int(result) == 0
    except Exception:
        return False
