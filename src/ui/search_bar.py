"""搜索栏（SearchBar）。

极简搜索输入，文本变化时发出 searchChanged 信号。
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLineEdit, QWidget


class SearchBar(QLineEdit):
    """单行搜索框，透传 textChanged。"""

    searchChanged = Signal(str)
    _DEBOUNCE_MS = 180

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("搜索文本或编码…")
        self.setClearButtonEnabled(True)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(lambda: self.searchChanged.emit(self.text()))
        self.textChanged.connect(lambda _text: self._debounce.start(self._DEBOUNCE_MS))
