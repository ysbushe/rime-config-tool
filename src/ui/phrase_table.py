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

from PySide6.QtCore import QItemSelectionModel, Qt, QRect, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyledItemDelegate,
    QTableView,
)

from src.repo.phrase_repo import Phrase
from src.service.group_service import GroupService
from src.ui.click_activated_combo import ClickActivatedComboBox
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


class CheckmarkDelegate(QStyledItemDelegate):
    """表格选择列使用自绘对勾，避免受缺失 QSS 图片资源影响。"""

    toggleRequested = Signal(int)

    def paint(self, painter, option, index) -> None:
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        side = min(16, option.rect.height() - 8)
        rect = QRect(
            option.rect.x() + (option.rect.width() - side) // 2,
            option.rect.y() + (option.rect.height() - side) // 2,
            side,
            side,
        )
        painter.save()
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2166A5"))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor("#FFFFFF"))
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(max(9, font.pointSize()))
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "✓")
        else:
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QColor("#A7B1BC"))
            painter.drawRoundedRect(rect, 3, 3)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == event.Type.MouseButtonRelease and option.rect.contains(event.pos()):
            self.toggleRequested.emit(index.row())
            return True
        return False


class PhraseTableView(QTableView):
    """词库条目表格视图。"""

    deleteRequested = Signal(object)        # Phrase
    cellEdited = Signal(int, int, str)      # row, col, new_text
    groupEdited = Signal(int, str)          # row, new_group
    checkChanged = Signal(str, bool)        # key, checked
    entryEditRequested = Signal(object)     # Phrase, text/code double click

    def __init__(self, group_service: Optional[GroupService] = None,
                 parent: object = None) -> None:
        super().__init__(parent)
        self._groups = group_service
        self._model = PhraseTableModel(group_service=group_service)
        self._model.cellEdited.connect(lambda r, c, t: self.cellEdited.emit(r, c, t))
        self._model.groupEdited.connect(lambda r, g: self.groupEdited.emit(r, g))
        self._model.selectionChanged.connect(self.checkChanged.emit)

        self._delegate = ButtonDelegate(self)
        self._delegate.deleteRequested.connect(self._on_delegate_delete)
        self._check_delegate = CheckmarkDelegate(self)
        self._check_delegate.toggleRequested.connect(self._toggle_row)

        self.setModel(self._model)
        self.setItemDelegateForColumn(COL_ACTION, self._delegate)
        self.setItemDelegateForColumn(COL_SELECT, self._check_delegate)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._batch_mode = False
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.verticalHeader().setVisible(False)
        self.setColumnWidth(COL_SELECT, 44)
        self.setColumnWidth(COL_TEXT, 210)
        self.setColumnWidth(COL_CODE, 170)
        self.setColumnWidth(COL_WEIGHT, 64)
        self.setColumnWidth(COL_GROUP, 110)
        self.setColumnWidth(COL_ACTION, 76)
        header = self.horizontalHeader()
        header.setSectionResizeMode(COL_CODE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ACTION, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        self.doubleClicked.connect(self._on_double_click)

        # 选择列默认隐藏（由批量选择按钮开启）
        self.setColumnHidden(COL_SELECT, True)
        self._drag_anchor_row: int | None = None
        self._dragging_batch = False

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
        elif index.column() in (COL_TEXT, COL_CODE):
            phrase = self._model.phrase_at_row(index.row())
            if phrase is not None:
                self.entryEditRequested.emit(phrase)

    def _open_group_editor(self, index) -> None:
        if self._groups is None:
            return
        combo = ClickActivatedComboBox(self)
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
        self._batch_mode = visible
        self.setColumnHidden(COL_SELECT, not visible)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection if visible
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        if not visible:
            self.clearSelection()

    def mousePressEvent(self, event) -> None:
        if self._batch_mode and event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() != COL_ACTION:
                row = index.row()
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self._drag_anchor_row is not None:
                    self._check_drag_range(self._drag_anchor_row, row)
                else:
                    # 无论点击文本还是左侧方框，均由同一模型状态即时切换。
                    self._model.toggle_checked_at(row)
                    self._drag_anchor_row = row
                    self._dragging_batch = True
                    self._select_drag_range(row, row)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._batch_mode and self._dragging_batch:
            index = self.indexAt(event.position().toPoint())
            if index.isValid() and index.column() != COL_ACTION and self._drag_anchor_row is not None:
                self._check_drag_range(self._drag_anchor_row, index.row())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._batch_mode and self._dragging_batch and event.button() == Qt.MouseButton.LeftButton:
            self._dragging_batch = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _check_drag_range(self, first_row: int, last_row: int) -> None:
        first, last = sorted((first_row, last_row))
        rows = [row for row in range(first, last + 1) if not self.isRowHidden(row)]
        self._model.check_rows(rows)
        self._select_drag_range(first, last)

    def _select_drag_range(self, first_row: int, last_row: int) -> None:
        selection = self.selectionModel()
        selection.clearSelection()
        first, last = sorted((first_row, last_row))
        for row in range(first, last + 1):
            if self.isRowHidden(row):
                continue
            selection.select(
                self._model.index(row, COL_TEXT),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        self.setCurrentIndex(self._model.index(last_row, COL_TEXT))

    def _toggle_row(self, row: int) -> None:
        if self._batch_mode:
            self._model.toggle_checked_at(row)
            self._drag_anchor_row = row

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
