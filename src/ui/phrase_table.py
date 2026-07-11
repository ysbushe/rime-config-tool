"""词库表格视图（PhraseTableView，基于 QTableView + PhraseTableModel）。

列：选择 | 文本 | 编码 | 权重 | 分组 | 操作
    - 最左『选择』列：CheckStateRole 渲染复选框（默认隐藏，由批量选择按钮开启）；
    - 文本 / 编码 / 权重：双击内联编辑（经 cellEdited 上报，由 PhraseManager 落内存）；
    - 分组列：双击弹出下拉框选择已有分组（经 groupEdited 上报）；
    - 操作列：委托绘制的『删除』按钮（点击 emit deleteRequested(Phrase)）；
    - 支持 apply_filter 行显隐（不重建），排序走 model.reorder（layoutChanged）。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QTableView,
)

from src.repo.phrase_repo import Phrase
from src.service.group_service import GroupService
from src.ui.phrase_table_model import (
    ButtonDelegate,
    COL_ACTION,
    COL_CODE,
    COL_GROUP,
    COL_SELECT,
    COL_TEXT,
    COL_WEIGHT,
    PhraseTableModel,
    UNGROUPED,
)


class PhraseTableView(QTableView):
    """词库条目表格视图。"""

    deleteRequested = Signal(object)        # Phrase
    cellEdited = Signal(int, int, str)      # row, col, new_text
    groupEdited = Signal(int, str)          # row, new_group
    selectionChanged = Signal(str, bool)    # key, checked

    def __init__(self, group_service: Optional[GroupService] = None,
                 parent: object = None) -> None:
        super().__init__(parent)
        self._groups = group_service
        self._model = PhraseTableModel(group_service=group_service)
        self._model.cellEdited.connect(lambda r, c, t: self.cellEdited.emit(r, c, t))
        self._model.groupEdited.connect(lambda r, g: self.groupEdited.emit(r, g))
        self._model.selectionChanged.connect(self.selectionChanged.emit)

        self._delegate = ButtonDelegate(self)
        self._delegate.deleteRequested.connect(self._on_delegate_delete)

        self.setModel(self._model)
        self.setItemDelegateForColumn(COL_ACTION, self._delegate)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.verticalHeader().setVisible(False)
        self.setColumnWidth(COL_SELECT, 44)
        self.setColumnWidth(COL_TEXT, 210)
        self.setColumnWidth(COL_CODE, 170)
        self.setColumnWidth(COL_WEIGHT, 64)
        self.setColumnWidth(COL_GROUP, 110)
        self.horizontalHeader().setStretchLastSection(True)
        self.doubleClicked.connect(self._on_double_click)

        # 选择列默认隐藏（由批量选择按钮开启）
        self.setColumnHidden(COL_SELECT, True)

    # ------------------------------------------------------------------ #
    def set_phrases(self, phrases: List[Phrase], group_of=None,
                    selected_of: Optional[Callable[[Phrase], bool]] = None, code_display=None) -> None:
        self._model.set_phrases(phrases, group_of=group_of, selected_of=selected_of,
                                code_display=code_display)

    def reorder(self, ordered: List[Phrase]) -> None:
        self._model.reorder(ordered)

    def _on_delegate_delete(self, row: int) -> None:
        p = self._model.phrase_at_row(row)
        if p is not None:
            self.deleteRequested.emit(p)

    # ------------------------------------------------------------------ #
    def _on_double_click(self, index) -> None:
        if index.column() == COL_GROUP:
            self._open_group_editor(index)

    def _open_group_editor(self, index) -> None:
        if self._groups is None:
            return
        combo = QComboBox(self)
        options = [UNGROUPED] + self._groups.list_groups()
        combo.addItems(options)
        current = self._model.data(index, Qt.ItemDataRole.DisplayRole) or UNGROUPED
        idx = combo.findText(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.setIndexWidget(index, combo)
        combo.currentTextChanged.connect(lambda _=None: self._commit_group(index, combo))
        combo.showPopup()

    def _commit_group(self, index, combo) -> None:
        text = combo.currentText()
        self.setIndexWidget(index, None)
        self._model.setData(index, "" if text == UNGROUPED else text,
                             Qt.ItemDataRole.EditRole)

    # ------------------------------------------------------------------ #
    def apply_filter(self, visible_keys: Optional[set] = None) -> None:
        """显隐行（不重建表格）。visible_keys=None 表示全部显示。"""
        self.setUpdatesEnabled(False)
        for row in range(self._model.rowCount()):
            key = self._model.key_at_row(row)
            show = (visible_keys is None) or (key in visible_keys)
            self.setRowHidden(row, not show)
        self.setUpdatesEnabled(True)

    def set_all_checked(self, state: bool) -> None:
        self._model.set_all_checked(state)

    def get_checked_keys(self) -> List[str]:
        return self._model.get_checked_keys()

    def update_group_cell(self, row: int, text: str) -> None:
        self._model.update_group_cell(row, text)

    def set_select_column_visible(self, visible: bool) -> None:
        self.setColumnHidden(COL_SELECT, not visible)

    def select_key(self, key: str) -> bool:
        """按词条键选中整行，并将该行置于视口顶部。"""
        for row in range(self._model.rowCount()):
            if self._model.key_at_row(row) != key or self.isRowHidden(row):
                continue
            index = self._model.index(row, COL_TEXT)
            self.clearSelection()
            self.setCurrentIndex(index)
            self.selectRow(row)
            self.scrollTo(
                index,
                QAbstractItemView.ScrollHint.PositionAtTop,
            )
            return True
        return False
