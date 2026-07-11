"""词条编辑弹窗（PhraseEditor）。

字段：
    - 文本（可编辑）
    - 编码（可手填任意字符串；默认 manual 模式）
    - 权重（可编辑整数，越大越靠前）
    - 分组（下拉；可空）

「生成全拼」按钮：直接生成默认无声调全拼，用户可在编码框手动修正。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.ui.encoding_suggestion_panel import EncodingSuggestionPanel
from src.service.pinyin_service import PinyinService


class PhraseEditor(QDialog):
    """编辑单条词库条目。"""

    def __init__(self, phrase: Phrase | None = None,
                 groups: Optional[List[str]] = None,
                 pinyin: PinyinService | None = None,
                 repo: PhraseRepo | None = None,
                 display_code: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑词条" if phrase else "新增词条")
        self._phrase = phrase
        self._groups = list(groups or [])
        self._pinyin = pinyin or PinyinService()
        self._repo = repo
        self._initial_display_code = display_code
        self._build_ui()
        self._fill(phrase)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        self._text = QLineEdit()
        self._code = QLineEdit()
        self._code.setPlaceholderText("可手填简码/缩写/英文 id；或点『生成全拼』")
        self._text.textEdited.connect(self._on_text_edited)

        self._weight = QSpinBox()
        self._code.textEdited.connect(self._normalize_code_input)
        self._weight.setRange(1, 99)
        self._weight.setValue(1)

        self._group = QComboBox()
        self._group.setEditable(False)
        self._group.addItem("（无）", "")
        for g in self._groups:
            self._group.addItem(g, g)

        # 生成全拼按钮
        self._btn_gen = QPushButton("生成全拼")
        self._btn_gen.setObjectName("Primary")
        self._btn_gen.clicked.connect(self._on_generate)

        code_row = QHBoxLayout()
        self._btn_separator = QPushButton("'")
        self._btn_separator.setFixedWidth(34)
        self._btn_separator.setToolTip("插入显示分隔符；保存时不会写入词库编码")
        self._btn_separator.clicked.connect(self._insert_separator)
        code_row.addWidget(self._code, 1)
        code_row.addWidget(self._btn_gen)

        form.addRow("文本：", self._text)
        form.addRow("编码：", code_row)
        code_row.addWidget(self._btn_separator)
        form.addRow("权重：", self._weight)
        form.addRow("分组：", self._group)
        layout.addLayout(form)

        self._err = QLabel("")
        self._err.setProperty("role", "error")
        self._suggestion_panel = EncodingSuggestionPanel(
            self._pinyin, self._repo, self
        )
        self._suggestion_panel.codeSelected.connect(self._set_suggested_code)
        layout.addWidget(self._suggestion_panel)

        layout.addWidget(self._err)

        # 中文按钮：保存 / 取消
        buttons = QDialogButtonBox()
        btn_save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_save.clicked.connect(self._on_accept)
        btn_cancel.clicked.connect(self.reject)
        btn_save.setDefault(True)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    def _fill(self, phrase: Phrase | None) -> None:
        if phrase is None:
            return
        self._text.setText(phrase.text)
        self._code.setText(self._initial_display_code or phrase.code)
        self._weight.setValue(phrase.weight)
        self._suggestion_panel.set_text(phrase.text)
        self._suggestion_panel.select_code(self._code.text())

    def set_group_hint(self, group: str) -> None:
        idx = self._group.findData(group)
        if idx >= 0:
            self._group.setCurrentIndex(idx)

    # ------------------------------------------------------------------ #
    def _on_generate(self) -> None:
        text = self._text.text().strip()
        if not text:
            self._err.setText("请先填写文本再生成全拼。")
            return
        suggestions = build_suggestions(text, self._pinyin)
        code = (
            suggestions[0].display_code if suggestions
            else self._pinyin.get_full_pinyin(text)
        )
        if not code:
            self._err.setText("未能生成全拼，可手动填写编码。")
            return
        self._code.setText(code)
        self._err.setText("")

    def _on_text_edited(self, text: str) -> None:
        self._suggestion_panel.set_text(text)

    def _set_suggested_code(self, code: str) -> None:
        self._code.setText(code)
        self._code.setFocus()
        self._code.setCursorPosition(len(code))

    def _normalize_code_input(self, value: str) -> None:
        normalized = normalize_display_code(value)
        if normalized != value:
            position = self._code.cursorPosition()
            self._code.blockSignals(True)
            self._code.setText(normalized)
            self._code.setCursorPosition(min(position, len(normalized)))
            self._code.blockSignals(False)
        self._suggestion_panel.select_code(normalized)

    def _insert_separator(self) -> None:
        value = self._code.text()
        position = self._code.cursorPosition()
        normalized = normalize_display_code(
            value[:position] + "'" + value[position:]
        )
        self._code.setText(normalized)
        self._code.setFocus()
        self._code.setCursorPosition(min(position + 1, len(normalized)))

    def _on_accept(self) -> None:
        text = self._text.text().strip()
        if not text:
            self._err.setText("文本不能为空。")
            return
        # 编码允许为空（RIME 允许仅文本行），但建议填写
        self._err.setText("")
        self.accept()

    # ------------------------------------------------------------------ #
    def get_values(self) -> dict:
        group_data = self._group.currentData() or ""
        display_code = normalize_display_code(self._code.text())
        return {
            "text": self._text.text().strip(),
            "code": raw_code(display_code),
            "display_code": display_code,
            "weight": self._weight.value(),
            "group": group_data,
            "weight_updates": self._suggestion_panel.weight_updates(),
        }
