"""Clickable encoding suggestions with inline duplicate-code weight editing."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import PhraseRepo
from src.service.pinyin_service import PinyinService


class EncodingSuggestionPanel(QWidget):
    codeSelected = Signal(str)

    def __init__(self, pinyin: PinyinService, repo: Optional[PhraseRepo] = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pinyin = pinyin
        self._repo = repo
        self._text = ""
        self._selected_code = ""
        self._weight_edits: dict[str, int] = {}
        self._buttons: list[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self._suggestions = QHBoxLayout()
        self._suggestions.setSpacing(6)
        root.addLayout(self._suggestions)

        self._conflict = QFrame()
        self._conflict.setObjectName("ConflictPanel")
        conflict_layout = QVBoxLayout(self._conflict)
        conflict_layout.setContentsMargins(8, 8, 8, 8)
        self._conflict_title = QLabel()
        conflict_layout.addWidget(self._conflict_title)
        self._grid = QGridLayout()
        self._grid.setColumnStretch(0, 1)
        conflict_layout.addLayout(self._grid)
        self._weight_hint = QLabel(
            "权重越大，同一编码内通常越靠前；最终顺序还会受翻译器优先级影响。"
        )
        self._weight_hint.setWordWrap(True)
        self._weight_hint.setProperty("role", "info")
        conflict_layout.addWidget(self._weight_hint)
        root.addWidget(self._conflict)
        self._conflict.hide()

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip()
        self._rebuild_suggestions()

    def select_code(self, display_code: str) -> None:
        self._selected_code = normalize_display_code(display_code)
        self._rebuild_conflicts()

    def weight_updates(self) -> dict[str, int]:
        return dict(self._weight_edits)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_suggestions(self) -> None:
        self._clear_layout(self._suggestions)
        self._buttons = []
        suggestions = build_suggestions(self._text, self._pinyin)
        for suggestion in suggestions:
            conflicts = self._conflicts_for(suggestion.raw_code)
            exact = any(p.text == self._text for p in conflicts)
            state = "已存在" if exact else (f"{len(conflicts)} 个同码" if conflicts else "空闲")
            button = QPushButton(f"{suggestion.label}  {suggestion.display_code}\n{state}")
            button.setCheckable(True)
            button.setToolTip("点击选用此编码；存在重码时在下方调整候选权重")
            button.clicked.connect(
                lambda _checked=False, code=suggestion.display_code: self._choose(code)
            )
            self._suggestions.addWidget(button)
            self._buttons.append(button)
        self._suggestions.addStretch(1)

    def _choose(self, display_code: str) -> None:
        self._selected_code = display_code
        self.codeSelected.emit(display_code)
        self._rebuild_conflicts()

    def _conflicts_for(self, code: str):
        if self._repo is None or not code:
            return []
        stored = raw_code(code)
        return [p for p in self._repo.all() if raw_code(p.code) == stored]

    def _rebuild_conflicts(self) -> None:
        self._clear_layout(self._grid)
        conflicts = self._conflicts_for(self._selected_code)
        if not conflicts:
            self._conflict.hide()
            return
        self._conflict_title.setText(
            f"编码 {normalize_display_code(self._selected_code)} 已有 {len(conflicts)} 个候选，可并列保存："
        )
        self._grid.addWidget(QLabel("已有词条"), 0, 0)
        self._grid.addWidget(QLabel("权重（1～99）"), 0, 1)
        ordered = sorted(conflicts, key=lambda p: p.weight, reverse=True)
        for row, phrase in enumerate(ordered, start=1):
            self._grid.addWidget(QLabel(phrase.text), row, 0)
            spin = QSpinBox()
            spin.setRange(1, 99)
            spin.setValue(max(1, min(99, phrase.weight)))
            spin.valueChanged.connect(
                lambda value, key=phrase.key: self._weight_edits.__setitem__(key, value)
            )
            self._grid.addWidget(spin, row, 1)
        self._conflict.show()
