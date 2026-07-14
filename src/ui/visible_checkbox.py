"""Checkbox with a guaranteed visible checkmark in Qt stylesheet themes."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton


class VisibleCheckBox(QCheckBox):
    """Keep native behavior while painting a checkmark without external image assets."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.checkState() == Qt.CheckState.Unchecked:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#FFFFFF"))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(rect.left() + 3, rect.center().y(),
                         rect.center().x() - 1, rect.bottom() - 4)
        painter.drawLine(rect.center().x() - 1, rect.bottom() - 4,
                         rect.right() - 3, rect.top() + 4)
        painter.end()
