"""搜索栏（SearchBar）。

极简搜索输入，文本变化时发出 searchChanged 信号。
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QWidget


class SearchBar(QLineEdit):
    """单行搜索框，透传 textChanged。"""

    searchChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("搜索文本或编码…")
        self.setClearButtonEnabled(True)
        self.textChanged.connect(self.searchChanged.emit)
