"""热键管理器（HotkeyManager）。

统一接口，自动选择后端：
    - 首选 Win32 RegisterHotKey 后端（确定性检测冲突、前台更稳）
    - 不可用时降级 keyboard 后端
    - 再不可用时降级 AHK v2 后端
    - 皆不可用则热键禁用（不影响其余功能）

回调签名：callback(text: str)，text 为抓取的选中文本。
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.service.hotkey_backends.ahk_backend import AhkBackend
from src.service.hotkey_backends.keyboard_backend import KeyboardBackend
from src.service.hotkey_backends.win32_backend import Win32HotkeyBackend
from src.utils.logger import get_logger

logger = get_logger(__name__)

_CAPTURE_DELAY_MS = 30


class HotkeyManager:
    """全局热键收藏管理器。"""

    def __init__(self) -> None:
        self._backend = None  # type: ignore
        self._callback: Optional[Callable[[str], None]] = None
        self._combo: Optional[str] = None
        self._capture_pending = False
        self._capture_generation = 0
        self._capture_results: queue.SimpleQueue[tuple[int, str]] = queue.SimpleQueue()
        # Keep the poll timer owned by QApplication.  The manager can be
        # recreated when settings change, while queued capture work may finish
        # a moment later.
        self._capture_poll = QTimer(QApplication.instance())
        self._capture_poll.setInterval(15)
        self._capture_poll.timeout.connect(self._drain_capture_results)

    def _drain_capture_results(self) -> None:
        """Deliver worker results from the GUI thread without cross-thread Qt calls."""
        delivered = False
        while True:
            try:
                generation, text = self._capture_results.get_nowait()
            except queue.Empty:
                break
            if generation == self._capture_generation and self._callback:
                self._callback(text)
                delivered = True
        if delivered or not self._capture_pending:
            self._capture_pending = False
            self._capture_poll.stop()

    # ------------------------------------------------------------------ #
    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "none"

    @property
    def available(self) -> bool:
        return self._backend is not None

    # ------------------------------------------------------------------ #
    def register(self, combo: str, callback: Callable[[str], None]) -> bool:
        # 先注销旧注册（旧组合 / 旧窗口），避免重复叠加导致一次按键弹多个窗口
        self.unregister()
        self._backend = self._select_backend()
        if self._backend is None:
            logger.warning("热键后端均不可用，热键收藏已禁用。")
            return False
        self._combo = combo
        self._callback = callback
        ok = self._backend.register(combo, self._make_handler())
        logger.info("热键注册：backend=%s combo=%s result=%s", self.backend_name, combo, ok)
        return ok

    def unregister(self) -> None:
        self._capture_generation += 1
        self._capture_pending = False
        self._capture_poll.stop()
        if self._backend is not None:
            self._backend.unregister()
            self._backend = None
            self._combo = None

    # ------------------------------------------------------------------ #
    def _select_backend(self):
        # 原生 Win32 RegisterHotKey 优先（可确定性检测冲突、前台更稳）
        win = Win32HotkeyBackend()
        if win.available():
            return win
        kb = KeyboardBackend()
        if kb.available():
            return kb
        ahk = AhkBackend()
        if ahk.available():
            return ahk
        return None

    def _make_handler(self) -> Callable[[], None]:
        backend = self._backend

        def run_capture(generation: int, target=None) -> None:
            text = ""
            try:
                logger.info("热键触发：开始取词 backend=%s", backend.name if backend else "none")
                if backend is not None and hasattr(backend, "capture_selection"):
                    if target is None:
                        text = backend.capture_selection()
                    else:
                        text = backend.capture_selection(target)
            except Exception as exc:
                logger.warning("抓取选中文本失败：%s", exc)
                text = ""
            logger.info("热键触发：取词结束 result=%s", "成功" if text else "空")
            self._capture_results.put((generation, text))

        def handler() -> None:
            if self._capture_pending:
                logger.info("热键取词仍在进行，忽略重复触发。")
                return
            self._capture_pending = True
            target = None
            if backend is not None and hasattr(backend, "capture_target"):
                try:
                    target = backend.capture_target()
                except Exception as exc:
                    logger.debug("记录热键目标窗口失败：%s", exc)
            logger.info("热键触发：收到系统热键，%sms 后取词", _CAPTURE_DELAY_MS)
            generation = self._capture_generation

            def start_worker(captured_target=target, captured_generation=generation) -> None:
                self._capture_poll.start()
                threading.Thread(
                    target=run_capture,
                    args=(captured_generation, captured_target),
                    name="rime-config-capture",
                    daemon=True,
                ).start()

            QTimer.singleShot(_CAPTURE_DELAY_MS, start_worker)

        return handler
