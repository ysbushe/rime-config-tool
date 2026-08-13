"""Dialogs for whole-library maintenance features."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from src.encoding.code_suggestions import raw_code
from src.repo.phrase_repo import Phrase
from src.ui.dialog_workbench import dialog_section
from src.ui.code_line_edit import CodeItemDelegate
from src.service.library_tools import (
    DuplicateIndex, HealthIssue, exact_duplicate_groups, export_text,
    parse_import_text,
)
from src.ui.phrase_table import PhraseTableView


class DuplicateEditDialog(QDialog):
    """Temporary duplicate editor. Row deletion is committed only by Save."""

    def __init__(self, title: str, phrases: list[Phrase], parent=None, display_code=None,
                 code_first: bool = False, code_alphabet: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle(title)
        self.resize(760, 560)
        self._original = list(phrases)
        self._display_code = display_code or (lambda phrase: phrase.code)
        self._code_first = code_first
        self._code_alphabet = code_alphabet

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        note, note_layout = dialog_section(self, "集中编辑")
        note_layout.addWidget(QLabel("可集中修改文本、编码和权重。删除仅在本窗口暂存，点击“保存全部”后才写入词库。", note))
        layout.addWidget(note)

        table_section, table_layout = dialog_section(self, "可编辑项目")
        self.table = QTableWidget(len(phrases), 4, table_section)
        self.table.setHorizontalHeaderLabels(["编码", "文本", "权重", "删除"] if self._code_first else ["文本", "编码", "权重", "删除"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setItemDelegateForColumn(
            0 if self._code_first else 1,
            CodeItemDelegate(self._code_alphabet, self.table),
        )
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 76)
        self.table.setColumnWidth(3, 66)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for row, item in enumerate(phrases):
            self._add_row(row, item)
        table_layout.addWidget(self.table, 1)
        layout.addWidget(table_section, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存全部")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(self, row: int, item: Phrase) -> None:
        first, second = (
            (self._display_code(item), item.text)
            if self._code_first else (item.text, self._display_code(item))
        )
        self.table.setItem(row, 0, QTableWidgetItem(first))
        self.table.setItem(row, 1, QTableWidgetItem(second))
        self.table.setRowHeight(row, 34)
        spin = QSpinBox(self.table)
        spin.setRange(1, 99)
        spin.setValue(item.weight)
        self.table.setCellWidget(row, 2, spin)
        delete = QPushButton("删除", self.table)
        delete.setObjectName("Danger")
        delete.clicked.connect(lambda _=False, r=row: self._confirm_remove(r))
        self.table.setCellWidget(row, 3, delete)

    def _show_context_menu(self, position) -> None:
        row = self.table.indexAt(position).row()
        if row < 0:
            return
        menu = QMenu(self)
        action = menu.addAction("删除此行")
        if menu.exec(self.table.viewport().mapToGlobal(position)) == action:
            self._confirm_remove(row)

    def _confirm_remove(self, row: int) -> None:
        if row < 0 or QMessageBox.question(
            self, "删除编码", "仅从本次编辑中移除此行；点击“保存全部”后才会写入词库。是否继续？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.table.removeRow(row)

    def entries(self) -> list[Phrase]:
        result: list[Phrase] = []
        for row in range(self.table.rowCount()):
            first = self.table.item(row, 0).text().strip() if self.table.item(row, 0) else ""
            second = self.table.item(row, 1).text().strip() if self.table.item(row, 1) else ""
            text, code = (second, first) if self._code_first else (first, second)
            weight_box = self.table.cellWidget(row, 2)
            weight = weight_box.value() if isinstance(weight_box, QSpinBox) else 1
            if text and code:
                result.append(Phrase(text, code, weight))
        return result

    @property
    def original(self) -> list[Phrase]:
        return self._original


class DuplicateManagerDialog(QDialog):
    """Whole-library duplicate browser, independent of current search or group."""

    def __init__(self, phrases: list[Phrase], apply_callback, parent=None,
                 display_code=None, code_alphabet: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("重码字符/文本")
        self.resize(820, 600)
        self._phrases = phrases
        self._index = DuplicateIndex.build(phrases)
        self._apply_callback = apply_callback
        self._display_code = display_code or (lambda phrase: phrase.code)
        self._code_alphabet = code_alphabet
        self._mode = "text"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        mode_section, mode_layout = dialog_section(self, "显示方式")
        top = QHBoxLayout()
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._text_mode = QPushButton("按重码文本显示", self)
        self._code_mode = QPushButton("按重码拼音显示", self)
        for button, mode in ((self._text_mode, "text"), (self._code_mode, "code")):
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, value=mode: self._set_mode(value))
            self._mode_group.addButton(button)
            top.addWidget(button)
        self._text_mode.setChecked(True)
        self._apply_mode_style()
        top.addStretch(1)
        mode_layout.addLayout(top)
        hint = QLabel("双击项目打开二级编辑窗口。", mode_section)
        hint.setProperty("role", "neutral")
        mode_layout.addWidget(hint)
        layout.addWidget(mode_section)

        table_section, table_layout = dialog_section(self, "重码项目")
        self.table = QTableWidget(0, 3, table_section)
        self.table.setHorizontalHeaderLabels(["项目", "关联项", "数量"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 58)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.doubleClicked.connect(self._edit_current)
        table_layout.addWidget(self.table, 1)
        layout.addWidget(table_section, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._text_mode.setChecked(mode == "text")
        self._code_mode.setChecked(mode == "code")
        self._apply_mode_style()
        self.refresh()

    def _apply_mode_style(self) -> None:
        for button in (self._text_mode, self._code_mode):
            button.setObjectName("Primary" if button.isChecked() else "")
            button.style().unpolish(button)
            button.style().polish(button)

    def refresh(self) -> None:
        groups = self._index.by_text if self._mode == "text" else self._index.by_code
        self.table.setRowCount(len(groups))
        for row, (key, phrases) in enumerate(sorted(groups.items())):
            related = "、".join(self._display_code(item) if self._mode == "text" else item.text for item in phrases)
            self.table.setItem(row, 0, QTableWidgetItem(key))
            self.table.setItem(row, 1, QTableWidgetItem(related))
            amount = QTableWidgetItem(str(len(phrases)))
            amount.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, amount)
            self.table.setRowHeight(row, 32)

    def _edit_current(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        key = self.table.item(row, 0).text()
        groups = self._index.by_text if self._mode == "text" else self._index.by_code
        phrases = list(groups.get(key, ()))
        title = ("编辑重码文本：" if self._mode == "text" else "编辑重码拼音：") + key
        dialog = DuplicateEditDialog(
            title, phrases, self, self._display_code,
            code_first=self._mode == "code", code_alphabet=self._code_alphabet,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_callback(dialog.original, dialog.entries())
            self._phrases = [p for p in self._phrases if p not in dialog.original] + dialog.entries()
            self._index = DuplicateIndex.build(self._phrases)
            self.refresh()


class ImportExportDialog(QDialog):
    def __init__(self, phrases: list[Phrase], import_callback, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("词库导入导出")
        self.resize(720, 470)
        self._import_callback = import_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        hint, hint_layout = dialog_section(self, "导入格式")
        hint_layout.addWidget(QLabel("每行：文本<Tab>编码<Tab>权重。支持 TSV 或 CSV；导入前会预览有效项与错误。", hint))
        layout.addWidget(hint)
        self.editor = QPlainTextEdit(self)
        self.editor.setPlainText(export_text(phrases))
        layout.addWidget(self.editor)
        self.status = QLabel("已载入当前词库，可修改后导入。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        preview = QPushButton("校验预览", self)
        preview.clicked.connect(self._preview)
        apply = QPushButton("确认导入", self)
        apply.setObjectName("Primary")
        apply.clicked.connect(self._apply)
        close = QPushButton("关闭", self)
        close.clicked.connect(self.reject)
        buttons.addWidget(preview)
        buttons.addWidget(apply)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _preview(self) -> tuple[list[Phrase], list[str]]:
        entries, errors = parse_import_text(self.editor.toPlainText())
        text = f"有效 {len(entries)} 条"
        if errors:
            text += "；" + "；".join(errors[:5])
        self.status.setText(text)
        return entries, errors

    def _apply(self) -> None:
        entries, errors = self._preview()
        if errors and QMessageBox.question(self, "存在格式问题", "存在无效行，是否仅导入有效项？") != QMessageBox.StandardButton.Yes:
            return
        if not entries:
            return
        self._import_callback(entries)
        self.accept()


class PhraseSearchResultDialog(QDialog):
    """Independent result window that reuses the library table actions."""

    def __init__(self, title: str, phrase_provider, edit_callback,
                 delete_callback, group_service=None, parent=None,
                 display_code=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle(title)
        self.resize(820, 460)
        self._phrase_provider = phrase_provider
        self._edit_callback = edit_callback
        self._delete_callback = delete_callback
        self._group_service = group_service
        self._display_code = display_code or (lambda phrase: phrase.code)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        intro, intro_layout = dialog_section(self, "搜索结果")
        self._summary = QLabel(intro)
        self._summary.setWordWrap(True)
        intro_layout.addWidget(self._summary)
        layout.addWidget(intro)

        self._table = PhraseTableView(group_service=group_service, parent=self)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.entryEditRequested.connect(self._edit)
        self._table.deleteRequested.connect(self._delete)
        layout.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        phrases = list(self._phrase_provider())
        group_of = (
            lambda phrase: self._group_service.get_entry_group(
                phrase.text, phrase.code
            )
        ) if self._group_service is not None else None
        self._table.set_phrases(
            phrases, group_of=group_of, code_display=self._display_code
        )
        self._summary.setText(
            f"共找到 {len(phrases)} 条。双击文本或编码可编辑，"
            "“删除”操作与词库管理界面一致。"
        )

    def _edit(self, phrase: Phrase) -> None:
        self._edit_callback(phrase)
        self.refresh()

    def _delete(self, phrase: Phrase) -> None:
        self._delete_callback(phrase)
        self.refresh()


class LibraryHealthDialog(QDialog):
    """Read-only structured health results with entry navigation."""

    def __init__(self, phrases: list[Phrase], issues: list[HealthIssue],
                 view_callback, parent=None, display_code=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("词库检查")
        self.resize(920, 620)
        self._phrases = list(phrases)
        self._issues = list(issues)
        self._view_callback = view_callback
        self._display_code = display_code or (lambda phrase: phrase.code)
        self._duplicate_keys: list[tuple[str, str]] = []
        self._other_keys: list[tuple[str, str]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        summary, summary_layout = dialog_section(self, "检查概览")
        self._summary = QLabel(summary)
        self._summary.setWordWrap(True)
        summary_layout.addWidget(self._summary)
        layout.addWidget(summary)

        duplicates, duplicates_layout = dialog_section(self, "完全重复条目")
        self._duplicates = QTableWidget(0, 5, duplicates)
        self._duplicates.setHorizontalHeaderLabels(
            ["文本", "编码", "条数", "权重", "操作"]
        )
        self._duplicates.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._duplicates.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        dup_header = self._duplicates.horizontalHeader()
        dup_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        dup_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4):
            dup_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._duplicates.verticalHeader().setDefaultSectionSize(36)
        self._duplicates.doubleClicked.connect(self._view_duplicate_row)
        duplicates_layout.addWidget(self._duplicates)
        layout.addWidget(duplicates, 1)

        other, other_layout = dialog_section(self, "其他待处理问题")
        self._other = QTableWidget(0, 5, other)
        self._other.setHorizontalHeaderLabels(["类型", "说明", "文本", "编码", "操作"])
        self._other.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        other_header = self._other.horizontalHeader()
        other_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        other_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        other_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        other_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        other_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._other.setMaximumHeight(180)
        self._other.doubleClicked.connect(self._view_other_row)
        other_layout.addWidget(self._other)
        layout.addWidget(other)

        actions = QHBoxLayout()
        close = QPushButton("关闭", self)
        close.clicked.connect(self.accept)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        groups = exact_duplicate_groups(self._phrases)
        other_issues = [issue for issue in self._issues if issue.kind != "exact_duplicate"]
        self._summary.setText(
            f"共检查 {len(self._phrases)} 条；发现 {len(groups)} 组完全重复，"
            f"其他问题 {len(other_issues)} 项。点击“查看”或双击一行打开独立搜索结果。"
        )
        self._duplicate_keys = [group.key for group in groups]
        self._duplicates.setRowCount(len(groups))
        for row, group in enumerate(groups):
            display = self._display_code(group.entries[0]) or group.code
            weights = "、".join(str(item.weight) for item in group.entries)
            values = [
                group.text or "（空）", display or "（空）",
                str(len(group.entries)), weights,
            ]
            for column, value in enumerate(values):
                self._duplicates.setItem(row, column, QTableWidgetItem(value))
            button = QPushButton("查看", self._duplicates)
            button.clicked.connect(
                lambda _=False, key=group.key: self._view_callback(*key)
            )
            self._duplicates.setCellWidget(row, 4, button)
            self._duplicates.setRowHeight(row, 36)
        self._duplicates.setVisible(bool(groups))

        self._other_keys = [(issue.text, raw_code(issue.code)) for issue in other_issues]
        self._other.setRowCount(len(other_issues))
        for row, issue in enumerate(other_issues):
            for column, value in enumerate(
                (issue.kind, issue.message, issue.text or "（空）", issue.code or "（空）")
            ):
                self._other.setItem(row, column, QTableWidgetItem(value))
            key = self._other_keys[row]
            button = QPushButton("查看", self._other)
            button.clicked.connect(
                lambda _=False, selected=key: self._view_callback(*selected)
            )
            self._other.setCellWidget(row, 4, button)
        self._other.setVisible(bool(other_issues))

    def _view_duplicate_row(self, index) -> None:
        if 0 <= index.row() < len(self._duplicate_keys):
            self._view_callback(*self._duplicate_keys[index.row()])

    def _view_other_row(self, index) -> None:
        if 0 <= index.row() < len(self._other_keys):
            self._view_callback(*self._other_keys[index.row()])
