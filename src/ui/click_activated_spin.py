"""Spin box that only responds to wheel changes after a click."""
from __future__ import annotations

from PySide6.QtWidgets import QSpinBox


class ClickActivatedSpinBox(QSpinBox):
    """Prevent accidental value changes while the surrounding page is scrolling."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wheel_armed = False

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        self._wheel_armed = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event) -> None:
        if self._wheel_armed and self.hasFocus():
            super().wheelEvent(event)
            return
        event.ignore()
