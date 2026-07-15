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
import re

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.ui.click_activated_spin import ClickActivatedSpinBox
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.encoding_suggestion_panel import EncodingSuggestionPanel
from src.ui.rime_preview_panel import RimePreviewPanel
from src.service.pinyin_service import PinyinService


class PhraseEditor(QDialog):
    """编辑单条词库条目。"""

    def __init__(self, phrase: Phrase | None = None,
                 groups: Optional[List[str]] = None,
                 pinyin: PinyinService | None = None,
                 repo: PhraseRepo | None = None,
                 display_code: str = "",
                 system_dictionary_index=None,
                 rime_preview_service=None,
                 create_group=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("编辑词条" if phrase else "新增词条")
        self.setFixedWidth(680)
        self._phrase = phrase
        self._groups = list(groups or [])
        self._pinyin = pinyin or PinyinService()
        self._repo = repo
        self._initial_display_code = display_code
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._create_group_callback = create_group
        self._additional_codes: list[str] = []
        self._build_ui()
        self._fill(phrase)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self._preview_panel = RimePreviewPanel(
            self._rime_preview_service, self._system_dictionary_index, self._repo, self,
        )
        layout.addWidget(self._preview_panel)
        form = QFormLayout()
        form.setSpacing(10)

        self._text = QLineEdit()
        self._code = QLineEdit()
        self._code.setPlaceholderText("可手填简码/缩写/英文 id；或点『生成全拼』")
        self._text.textEdited.connect(self._on_text_edited)

        self._weight = ClickActivatedSpinBox()
        self._code.textEdited.connect(self._normalize_code_input)
        self._weight.setRange(1, 99)
        self._weight.setValue(1)

        self._group = ClickActivatedComboBox()
        self._group.setEditable(False)
        self._group.addItem("（无）", "")
        for g in self._groups:
            self._group.addItem(g, g)
        self._btn_new_group = QPushButton("新建分组")
        self._btn_new_group.setFixedWidth(82)
        self._btn_new_group.clicked.connect(self._create_group)

        # 生成全拼按钮
        self._btn_gen = QPushButton("生成全拼")
        self._btn_gen.setObjectName("Primary")
        self._btn_gen.clicked.connect(self._on_generate)

        code_row = QHBoxLayout()
        self._btn_separator = QPushButton("'")
        self._btn_separator.setFixedWidth(34)
        self._btn_separator.setToolTip("插入显示分隔符；保存时不会写入词库编码")
        self._btn_separator.clicked.connect(self._insert_separator)
        self._btn_add_code = QPushButton("增加编码")
        self._btn_add_code.setMinimumWidth(82)
        self._btn_add_code.clicked.connect(self._add_current_code)
        code_row.addWidget(self._code, 1)
        code_row.addWidget(self._btn_gen)
        code_row.addWidget(self._btn_separator)
        code_row.addWidget(self._btn_add_code)
        self._extra_code_box = QWidget()
        self._extra_code_layout = QVBoxLayout(self._extra_code_box)
        self._extra_code_layout.setContentsMargins(0, 4, 0, 0)
        self._extra_code_layout.setSpacing(4)
        self._extra_code_box.hide()
        code_container = QWidget()
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(0, 0, 0, 0)
        code_container_layout.setSpacing(0)
        code_container_layout.addLayout(code_row)
        code_container_layout.addWidget(self._extra_code_box)

        form.addRow("文本：", self._text)
        form.addRow("编码：", code_container)
        form.addRow("权重：", self._weight)
        group_row = QHBoxLayout()
        group_row.addWidget(self._group, 1)
        group_row.addWidget(self._btn_new_group)
        form.addRow("分组：", group_row)
        self._english_upper = QCheckBox("英文输出为大写（编码保持小写）")
        self._english_upper.setEnabled(False)
        form.addRow("英文：", self._english_upper)
        info_section = QFrame(self)
        info_section.setObjectName("DialogSection")
        info_layout = QVBoxLayout(info_section)
        info_layout.setContentsMargins(12, 10, 12, 12)
        info_title = QLabel("词条信息")
        info_title.setObjectName("DialogSectionTitle")
        info_layout.addWidget(info_title)
        info_layout.addLayout(form)
        layout.addWidget(info_section)

        self._err = QLabel("")
        self._err.setProperty("role", "error")
        self._suggestion_panel = EncodingSuggestionPanel(
            self._pinyin, self._repo, self
        )
        self._suggestion_panel.codeSelected.connect(self._set_suggested_code)
        self._suggestion_panel.codeAddRequested.connect(self._add_code)
        suggestion_section = QFrame(self)
        suggestion_section.setObjectName("DialogSection")
        suggestion_layout = QVBoxLayout(suggestion_section)
        suggestion_layout.setContentsMargins(12, 10, 12, 12)
        suggestion_title = QLabel("待选编码")
        suggestion_title.setObjectName("DialogSectionTitle")
        suggestion_layout.addWidget(suggestion_title)
        suggestion_layout.addWidget(self._suggestion_panel)
        layout.addWidget(suggestion_section)

        layout.addWidget(self._err)

        # 中文按钮：保存 / 取消
        buttons = QDialogButtonBox()
        btn_save = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_save.setObjectName("Primary")
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
        self._refresh_preview()

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
        self._refresh_preview()

    def _on_text_edited(self, text: str) -> None:
        self._update_english_output_option(text)
        self._suggestion_panel.set_text(text)
        self._refresh_preview()

    def _update_english_output_option(self, text: str) -> None:
        enabled = bool(re.fullmatch(r"[A-Za-z][A-Za-z -]*", (text or "").strip()))
        self._english_upper.setEnabled(enabled)
        if not enabled:
            self._english_upper.setChecked(False)

    def _stored_text_and_display_code(self) -> tuple[str, str]:
        text = self._text.text().strip()
        display = normalize_display_code(self._code.text())
        if self._english_upper.isChecked():
            text = text.upper()
            display = display.lower()
        return text, display

    def _add_current_code(self) -> None:
        self._add_code(self._code.text())

    def _add_code(self, code: str) -> None:
        display = normalize_display_code(code)
        if not raw_code(display):
            self._err.setText("请先填写可用编码。")
            return
        if display not in self._additional_codes:
            self._additional_codes.append(display)
        self._rebuild_extra_codes()

    def _remove_code(self, display: str) -> None:
        self._additional_codes = [item for item in self._additional_codes if item != display]
        self._rebuild_extra_codes()

    def _rebuild_extra_codes(self) -> None:
        while self._extra_code_layout.count():
            item = self._extra_code_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, display in enumerate(self._additional_codes, start=1):
            row = QWidget(self._extra_code_box)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f"编码{index}")
            label.setObjectName("ExtraCodeName")
            value = QLineEdit(display)
            value.setReadOnly(True)
            remove = QPushButton("删除")
            remove.setObjectName("SuggestionAdd")
            remove.clicked.connect(lambda _=False, code=display: self._remove_code(code))
            row_layout.addWidget(label)
            row_layout.addWidget(value, 1)
            row_layout.addWidget(remove)
            self._extra_code_layout.addWidget(row)
        self._extra_code_box.setVisible(bool(self._additional_codes))
        self._suggestion_panel.set_excluded_codes(self._additional_codes)
        self._refresh_preview()

    def _set_suggested_code(self, code: str) -> None:
        self._code.setText(code)
        self._code.setFocus()
        self._code.setCursorPosition(len(code))
        self._refresh_preview()

    def _normalize_code_input(self, value: str) -> None:
        normalized = normalize_display_code(value)
        if normalized != value:
            position = self._code.cursorPosition()
            self._code.blockSignals(True)
            self._code.setText(normalized)
            self._code.setCursorPosition(min(position, len(normalized)))
            self._code.blockSignals(False)
        self._suggestion_panel.select_code(normalized)
        self._refresh_preview()

    def _insert_separator(self) -> None:
        value = self._code.text()
        position = self._code.cursorPosition()
        normalized = normalize_display_code(
            value[:position] + "'" + value[position:]
        )
        self._code.setText(normalized)
        self._code.setFocus()
        self._code.setCursorPosition(min(position + 1, len(normalized)))
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        self._preview_panel.set_query(self._text.text(), self._code.text())

    def _create_group(self) -> None:
        if self._create_group_callback is None:
            self._err.setText("当前词库未启用分组。")
            return
        name, accepted = QInputDialog.getText(self, "新建分组", "分组名称：")
        name = name.strip()
        if not accepted or not name:
            return
        if name not in self._groups and not self._create_group_callback(name):
            self._err.setText("分组创建失败或名称已存在。")
            return
        if name not in self._groups:
            self._groups.append(name)
            self._group.addItem(name, name)
        self._group.setCurrentIndex(self._group.findData(name))
        self._err.setText("")

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
        stored_text, display_code = self._stored_text_and_display_code()
        return {
            "text": stored_text,
            "code": raw_code(display_code),
            "display_code": display_code,
            "weight": self._weight.value(),
            "group": group_data,
            "weight_updates": self._suggestion_panel.weight_updates(),
            "additional_codes": list(self._additional_codes),
        }
