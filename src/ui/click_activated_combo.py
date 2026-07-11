"""仅在鼠标点击激活后响应滚轮的下拉框。"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class ClickActivatedComboBox(QComboBox):
    """未点击时把滚轮事件交给父级滚动区域。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wheel_armed = False

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        super().mousePressEvent(event)
        # 弹出菜单会短暂触发失焦，因此在系统点击处理后再许可滚轮。
        self._wheel_armed = True

    def focusOutEvent(self, event) -> None:
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event) -> None:
        if self._wheel_armed and self.hasFocus():
            super().wheelEvent(event)
            return
        event.ignore()
