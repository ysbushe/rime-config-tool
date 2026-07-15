"""Application-owned lower-right notification, used when Windows suppresses tray balloons."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
import re
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class ToastNotification(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("ResultToast")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(330)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title_row = QHBoxLayout()
        self._title = QLabel("RIME 配置小工具")
        self._title.setObjectName("ResultToastTitle")
        self._close = QPushButton("×")
        self._close.setObjectName("ToastClose")
        self._close.setFixedSize(24, 24)
        self._close.setToolTip("关闭提示")
        self._close.clicked.connect(self.hide)
        title_row.addWidget(self._title, 1)
        title_row.addWidget(self._close)
        self._message = QLabel()
        self._message.setObjectName("ResultToastMessage")
        self._message.setWordWrap(True)
        self._detail = QWidget()
        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(3)
        detail_row = QHBoxLayout()
        detail_row.setContentsMargins(0, 0, 0, 0)
        detail_row.setSpacing(4)
        self._detail_prefix = QLabel()
        self._detail_text = QLabel()
        self._detail_text.setObjectName("ToastPhrase")
        self._detail_middle = QLabel()
        self._detail_codes = QLabel()
        self._detail_codes.setObjectName("ToastCodes")
        self._detail_codes.setWordWrap(True)
        detail_row.addWidget(self._detail_prefix)
        detail_row.addWidget(self._detail_text)
        detail_row.addWidget(self._detail_middle)
        detail_row.addStretch(1)
        detail_layout.addLayout(detail_row)
        detail_layout.addWidget(self._detail_codes)
        layout.addLayout(title_row)
        layout.addWidget(self._message)
        layout.addWidget(self._detail)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_result(self, ok: bool, message: str) -> None:
        self.setProperty("role", "success" if ok else "error")
        match = re.match(r"^(.*?)(「[^」]+」)(.*?：)(.+?。?)$", message)
        if match:
            self._message.hide()
            self._detail_prefix.setText(match.group(1))
            self._detail_text.setText(match.group(2))
            self._detail_middle.setText(match.group(3))
            self._detail_codes.setText(match.group(4))
            self._detail.show()
        else:
            self._detail.hide()
            self._message.setText(message)
            self._message.show()
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 18, area.bottom() - self.height() - 18)
        self.show()
        self.raise_()
        self._timer.start(10000)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.hide()
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.hide()
        event.accept()
