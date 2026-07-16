"""Clickable encoding suggestions with inline duplicate-code weight editing."""
from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from src.encoding.code_suggestions import EncodingSuggestion, build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import PhraseRepo
from src.service.pinyin_service import PinyinService
from src.ui.click_activated_spin import ClickActivatedSpinBox


class EncodingSuggestionOption(QFrame):
    """One compact, keyboard-accessible encoding suggestion row."""

    clicked = Signal(str)
    addRequested = Signal(str)

    def __init__(self, label: str, code: str, state: str, state_role: str, parent=None) -> None:
        super().__init__(parent)
        self._code = code
        self.setObjectName("EncodingSuggestion")
        self.setProperty("state", state_role)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QGridLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setHorizontalSpacing(12)
        row.setVerticalSpacing(0)
        row.setColumnMinimumWidth(0, 86)
        row.setColumnStretch(1, 1)
        row.setColumnMinimumWidth(2, 62)
        row.setColumnMinimumWidth(3, 70)
        name = QLabel(label)
        name.setObjectName("SuggestionName")
        name.setFixedWidth(86)
        code_label = QLabel(code)
        code_label.setObjectName("SuggestionCode")
        if label.startswith("英文") or label == "原样保留":
            code_label.setMaximumWidth(240)
        status = QLabel(state)
        status.setObjectName("SuggestionState")
        status.setProperty("role", state_role)
        status.setFixedWidth(62)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add = QPushButton("增加")
        add.setObjectName("SuggestionAdd")
        add.setFixedWidth(70)
        add.setToolTip("将该编码加入本次收藏，可一次保存多个编码")
        add.clicked.connect(lambda: self.addRequested.emit(self._code))
        row.addWidget(name, 0, 0)
        row.addWidget(code_label, 0, 1)
        row.addWidget(status, 0, 2)
        row.addWidget(add, 0, 3)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._code)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._code)
            event.accept()
            return
        super().keyPressEvent(event)


class EncodingSuggestionPanel(QWidget):
    codeSelected = Signal(str)
    codeAddRequested = Signal(str)

    def __init__(self, pinyin: PinyinService, repo: Optional[PhraseRepo] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pinyin = pinyin
        self._repo = repo
        self._text = ""
        self._selected_code = ""
        self._excluded_codes: set[str] = set()
        self._weight_edits: dict[str, int] = {}
        self._buttons: list[EncodingSuggestionOption] = []
        self._prepared_suggestions: list[EncodingSuggestion] | None = None
        self._conflicts_by_code: dict[str, list] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self._suggestions = QVBoxLayout()
        self._suggestions.setSpacing(5)
        root.addLayout(self._suggestions)
        self._loading = QLabel("正在准备编码建议…")
        self._loading.setProperty("role", "info")
        root.addWidget(self._loading)

        self._conflict = QFrame()
        self._conflict.setObjectName("ConflictPanel")
        conflict_layout = QVBoxLayout(self._conflict)
        conflict_layout.setContentsMargins(8, 8, 8, 8)
        self._conflict_title = QLabel()
        conflict_layout.addWidget(self._conflict_title)
        self._grid = QGridLayout()
        self._grid.setColumnStretch(0, 1)
        conflict_layout.addLayout(self._grid)
        self._weight_hint = QLabel("权重越大，同一编码内通常越靠前；最终顺序还会受翻译器优先级影响。")
        self._weight_hint.setWordWrap(True)
        self._weight_hint.setProperty("role", "info")
        conflict_layout.addWidget(self._weight_hint)
        root.addWidget(self._conflict)
        self._conflict.hide()
        # Four candidate rows are the normal shape. Keeping this space avoids
        # shifting every control below the panel when asynchronous results arrive.
        self.setMinimumHeight(178)

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip()
        self._prepared_suggestions = None
        self._conflicts_by_code = self._index_conflicts(self._repo.all() if self._repo else [])
        self._rebuild_suggestions()

    def set_loading(self, text: str) -> None:
        self._text = (text or "").strip()
        self._prepared_suggestions = None
        self._clear_layout(self._suggestions)
        self._buttons = []
        self._conflict.hide()
        self._loading.setVisible(bool(self._text))

    def set_prepared_suggestions(
            self, text: str, suggestions: Iterable[EncodingSuggestion],
            conflicts_by_code: dict[str, list] | None = None) -> None:
        self._text = (text or "").strip()
        self._prepared_suggestions = list(suggestions)
        self._conflicts_by_code = conflicts_by_code or {}
        self._loading.hide()
        self._rebuild_suggestions()

    def select_code(self, display_code: str) -> None:
        self._selected_code = normalize_display_code(display_code)
        for option in self._buttons:
            option.set_selected(option._code == self._selected_code)
        self._rebuild_conflicts()

    def weight_updates(self) -> dict[str, int]:
        return dict(self._weight_edits)

    def set_excluded_codes(self, codes: list[str]) -> None:
        self._excluded_codes = {raw_code(code) for code in codes if raw_code(code)}
        self._rebuild_suggestions()

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_suggestions(self) -> None:
        self._clear_layout(self._suggestions)
        self._buttons = []
        suggestions = self._prepared_suggestions
        if suggestions is None:
            suggestions = build_suggestions(self._text, self._pinyin)
        self._loading.setVisible(not bool(suggestions) and bool(self._text))
        for suggestion in suggestions:
            if suggestion.raw_code in self._excluded_codes:
                continue
            conflicts = self._conflicts_for(suggestion.raw_code)
            exact = any(p.text == self._text for p in conflicts)
            if exact:
                state, role = "已存在", "duplicate"
            elif conflicts:
                state, role = f"{len(conflicts)} 个同码", "warning"
            else:
                state, role = "空闲", "success"
            option = EncodingSuggestionOption(
                suggestion.label, suggestion.display_code, state, role, self
            )
            option.clicked.connect(self._choose)
            option.addRequested.connect(self.codeAddRequested.emit)
            option.set_selected(suggestion.display_code == self._selected_code)
            self._suggestions.addWidget(option)
            self._buttons.append(option)

    def _choose(self, display_code: str) -> None:
        self._selected_code = display_code
        self.codeSelected.emit(display_code)
        self.select_code(display_code)

    def _conflicts_for(self, code: str):
        if not code:
            return []
        return self._conflicts_by_code.get(raw_code(code), [])

    @staticmethod
    def _index_conflicts(phrases) -> dict[str, list]:
        indexed: dict[str, list] = {}
        for phrase in phrases:
            code = raw_code(phrase.code)
            if code:
                indexed.setdefault(code, []).append(phrase)
        return indexed

    def _rebuild_conflicts(self) -> None:
        self._clear_layout(self._grid)
        conflicts = self._conflicts_for(self._selected_code)
        if not conflicts:
            self._conflict.hide()
            return
        self._conflict_title.setText(f"编码 {normalize_display_code(self._selected_code)} 已有 {len(conflicts)} 个候选，可并列保存：")
        self._grid.addWidget(QLabel("已有词条"), 0, 0)
        self._grid.addWidget(QLabel("权重（1-99）"), 0, 1)
        for row, phrase in enumerate(sorted(conflicts, key=lambda p: p.weight, reverse=True), start=1):
            self._grid.addWidget(QLabel(phrase.text), row, 0)
            spin = ClickActivatedSpinBox()
            spin.setRange(1, 99)
            spin.setValue(max(1, min(99, phrase.weight)))
            spin.valueChanged.connect(lambda value, key=phrase.key: self._weight_edits.__setitem__(key, value))
            self._grid.addWidget(spin, row, 1)
        self._conflict.show()
