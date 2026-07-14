"""Batch editor for every code belonging to one phrase text."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.pinyin_service import PinyinService
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.click_activated_spin import ClickActivatedSpinBox
from src.ui.encoding_suggestion_panel import EncodingSuggestionPanel
from src.ui.rime_preview_panel import RimePreviewPanel


class MultiCodeEditor(QDialog):
    """Edit all codes for one text and validate them as a batch."""

    def __init__(self, text: str, phrases: list[Phrase], repo: PhraseRepo,
                 pinyin: PinyinService, display_for, parent=None, *,
                 groups: list[str] | None = None, group: str = "",
                 system_dictionary_index=None, rime_preview_service=None,
                 create_group=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑多个编码")
        self.setFixedWidth(680)
        self._repo = repo
        self._pinyin = pinyin
        self._display_for = display_for
        self._groups = list(groups or [])
        self._create_group_callback = create_group
        self._rows: list[tuple[QLineEdit, ClickActivatedSpinBox, QPushButton]] = []
        layout = QVBoxLayout(self)
        self._preview_panel = RimePreviewPanel(
            rime_preview_service, system_dictionary_index, repo, self,
        )
        layout.addWidget(self._preview_panel)
        form = QFormLayout()
        self._text = QLineEdit(text)
        self._text.setPlaceholderText("文本")
        self._text.textEdited.connect(self._on_text_edited)
        form.addRow("文本：", self._text)
        self._group = ClickActivatedComboBox()
        self._group.addItem("（无）", "")
        for item in self._groups:
            self._group.addItem(item, item)
        selected = self._group.findData(group)
        if selected >= 0:
            self._group.setCurrentIndex(selected)
        self._btn_new_group = QPushButton("新建分组")
        self._btn_new_group.setFixedWidth(82)
        self._btn_new_group.clicked.connect(self._create_group)
        group_row = QHBoxLayout()
        group_row.addWidget(self._group, 1)
        group_row.addWidget(self._btn_new_group)
        form.addRow("分组：", group_row)
        layout.addLayout(form)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._rows_layout)
        for phrase in phrases:
            self._add_row(display_for(phrase), phrase.weight)

        add = QPushButton("新增编码")
        add.setMinimumWidth(82)
        add.clicked.connect(lambda: self._add_row("", 1))
        layout.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)

        self._suggestions = EncodingSuggestionPanel(pinyin, repo, self)
        self._suggestions.set_text(text)
        self._suggestions.codeSelected.connect(self._add_suggested_code)
        self._suggestions.codeAddRequested.connect(self._add_suggested_code)
        self._sync_suggestions()
        layout.addWidget(self._suggestions)

        self._error = QLabel()
        self._error.setProperty("role", "error")
        self._error.setWordWrap(True)
        layout.addWidget(self._error)
        buttons = QDialogButtonBox()
        save = buttons.addButton("保存全部", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        save.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_preview()

    def _on_text_edited(self, text: str) -> None:
        self._suggestions.set_text(text)
        self._refresh_preview()

    def group_value(self) -> str:
        return self._group.currentData() or ""

    def _create_group(self) -> None:
        if self._create_group_callback is None:
            self._error.setText("当前词库未启用分组。")
            return
        name, accepted = QInputDialog.getText(self, "新建分组", "分组名称：")
        name = name.strip()
        if not accepted or not name:
            return
        if name not in self._groups and not self._create_group_callback(name):
            self._error.setText("分组创建失败或名称已存在。")
            return
        if name not in self._groups:
            self._groups.append(name)
            self._group.addItem(name, name)
        self._group.setCurrentIndex(self._group.findData(name))
        self._error.setText("")

    def text_value(self) -> str:
        return self._text.text().strip()

    def _add_suggested_code(self, code: str) -> None:
        normalized = normalize_display_code(code)
        raw = raw_code(normalized)
        if raw and any(raw_code(line.text()) == raw for line, _spin, _remove in self._rows):
            self._error.setText(f"编码 {normalized} 已在上方。")
            return
        self._error.setText("")
        self._add_row(normalized, 1)

    def _add_row(self, code: str, weight: int) -> None:
        row = QWidget(self)
        line = QLineEdit(normalize_display_code(code))
        line.setPlaceholderText("编码")
        line.textEdited.connect(lambda _text: self._on_code_edited())
        line.selectionChanged.connect(self._refresh_preview)
        spin = ClickActivatedSpinBox()
        spin.setRange(1, 99)
        spin.setValue(max(1, min(99, int(weight))))
        remove = QPushButton("删除")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line, 1)
        layout.addWidget(spin)
        layout.addWidget(remove)
        remove.clicked.connect(lambda: self._remove_row(row))
        self._rows_layout.addWidget(row)
        self._rows.append((line, spin, remove))
        if hasattr(self, "_suggestions"):
            self._sync_suggestions()
        self._refresh_preview()

    def _on_code_edited(self) -> None:
        self._sync_suggestions()
        self._refresh_preview()

    def _preview_code(self) -> str:
        focused = self.focusWidget()
        if isinstance(focused, QLineEdit) and any(focused is line for line, _spin, _remove in self._rows):
            return focused.text()
        return self._rows[0][0].text() if self._rows else ""

    def _refresh_preview(self) -> None:
        self._preview_panel.set_query(self._text.text(), self._preview_code())

    def _sync_suggestions(self) -> None:
        if not hasattr(self, "_suggestions"):
            return
        self._suggestions.set_excluded_codes([
            line.text() for line, _spin, _remove in self._rows if raw_code(line.text())
        ])

    def _remove_row(self, widget: QWidget) -> None:
        if len(self._rows) <= 1:
            self._error.setText("至少保留一个编码。")
            return
        for index, row in enumerate(self._rows):
            if row[0].parentWidget() is widget:
                self._rows.pop(index)
                break
        self._rows_layout.removeWidget(widget)
        widget.deleteLater()
        self._sync_suggestions()
        self._refresh_preview()

    def _accept(self) -> None:
        if not self.text_value():
            self._error.setText("文本不能为空。")
            return
        entries = self.entries()
        if not entries:
            self._error.setText("请至少填写一个编码。")
            return
        codes = [item["code"] for item in entries]
        if len(codes) != len(set(codes)):
            self._error.setText("同一文本不能重复使用相同编码。")
            return
        self._error.setText("")
        self.accept()

    def entries(self) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for line, spin, _remove in self._rows:
            display = normalize_display_code(line.text())
            code = raw_code(display)
            if code:
                values.append({"code": code, "display_code": display, "weight": spin.value()})
        return values

    def weight_updates(self) -> dict[str, int]:
        return self._suggestions.weight_updates()
