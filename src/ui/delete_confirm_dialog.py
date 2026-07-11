"""统一的删除确认对话框。"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DeleteConfirmDialog(QDialog):
    """等宽是/否按钮，默认安全选择为“否”。"""

    def __init__(
        self,
        title: str,
        message: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setSpacing(16)

        label = QLabel(message)
        label.setWordWrap(True)
        root.addWidget(label)

        actions = QHBoxLayout()
        actions.setSpacing(12)

        self.yes_button = QPushButton("是")
        self.yes_button.setObjectName("Danger")
        self.yes_button.setMinimumHeight(36)
        self.yes_button.clicked.connect(self.accept)

        self.no_button = QPushButton("否")
        self.no_button.setObjectName("Primary")
        self.no_button.setMinimumHeight(36)
        self.no_button.setDefault(True)
        self.no_button.setAutoDefault(True)
        self.no_button.clicked.connect(self.reject)

        actions.addWidget(self.yes_button, 1)
        actions.addWidget(self.no_button, 1)
        root.addLayout(actions)

        QTimer.singleShot(0, self.no_button.setFocus)


def confirm_delete(
    parent: QWidget,
    title: str,
    message: str,
) -> bool:
    dialog = DeleteConfirmDialog(title, message, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted
