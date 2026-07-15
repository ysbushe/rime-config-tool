"""Choose which codes of a phrase should be removed."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from src.repo.phrase_repo import Phrase
from src.ui.dialog_workbench import dialog_section


class _CodeChoiceRow(QWidget):
    def __init__(self, phrase: Phrase, checked: bool, parent=None) -> None:
        super().__init__(parent)
        self.phrase = phrase
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(8)
        code = QLabel(phrase.code)
        code.setObjectName("DeleteCode")
        code.setMinimumWidth(92)
        weight_label = QLabel("权重")
        weight = QLabel(str(phrase.weight))
        weight.setObjectName("DeleteWeight")
        weight.setFixedWidth(30)
        row.addWidget(self.checkbox)
        row.addWidget(code)
        row.addWidget(weight_label)
        row.addWidget(weight)
        row.addStretch(1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.setChecked(not self.checkbox.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)


class CodeDeleteDialog(QDialog):
    def __init__(self, text: str, phrases: list[Phrase], current_code: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("删除编码")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        choices, choices_layout = dialog_section(self, f"选择要删除「{text}」的编码")
        actions = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("取消全选")
        actions.addWidget(select_all)
        actions.addWidget(clear_all)
        actions.addStretch(1)
        choices_layout.addLayout(actions)
        self._checks: list[tuple[Phrase, QCheckBox]] = []
        for phrase in phrases:
            row = _CodeChoiceRow(phrase, phrase.code == current_code, choices)
            choices_layout.addWidget(row)
            self._checks.append((phrase, row.checkbox))
        layout.addWidget(choices)
        select_all.clicked.connect(lambda: self._set_all(True))
        clear_all.clicked.connect(lambda: self._set_all(False))
        self._error = QLabel()
        self._error.setProperty("role", "error")
        layout.addWidget(self._error)
        buttons = QDialogButtonBox()
        delete = buttons.addButton("删除所选编码", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        delete.setObjectName("Danger")
        delete.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        for _phrase, checkbox in self._checks:
            checkbox.setChecked(checked)

    def _accept(self) -> None:
        if not self.selected():
            self._error.setText("请至少选择一个编码。")
            return
        self.accept()

    def selected(self) -> list[Phrase]:
        return [phrase for phrase, checkbox in self._checks if checkbox.isChecked()]
