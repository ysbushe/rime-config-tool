"""词库表格的数据模型与操作列委托（为大数据量排序/切换流畅而设计）。

相比旧 QTableWidget 整表重建，本模型：
    - 选择列用 Qt.CheckStateRole 渲染（无 QWidget，零创建开销，且可经 QSS 美化）；
    - 操作列用 ButtonDelegate 绘制「删除」按钮（paint + 命中测试，无 setIndexWidget）；
    - 文本 / 编码 / 权重 走 Model 原生可编辑；
    - 排序仅重排内部 list 并 layoutChanged，View 自动重绘，**不重建任何控件**，
      几千条切换仍流畅（为未来词库膨胀打底）。
列：选择 | 文本 | 编码 | 权重 | 分组 | 操作
"""
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    Qt,
    QRect,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionButton,
)

from src.repo.phrase_repo import Phrase
from src.service.group_service import GroupService
from src.ui.theme import conflict_background

# 列索引（与旧 phrase_table 保持一致，便于增量迁移）
COL_SELECT = 0
COL_TEXT = 1
COL_CODE = 2
COL_WEIGHT = 3
COL_GROUP = 4
COL_ACTION = 5

UNGROUPED = "未分组"


class PhraseTableModel(QAbstractTableModel):
    """词库条目表模型。"""

    cellEdited = Signal(int, int, str)      # row, col, new_text
    groupEdited = Signal(int, str)          # row, new_group（空串表示未分组）
    selectionChanged = Signal(str, bool)    # key, checked

    def __init__(self, group_service: GroupService | None = None,
                 parent: object = None) -> None:
        super().__init__(parent)
        self._groups = group_service
        self._phrases: list[Phrase] = []
        self._checked: dict[str, bool] = {}     # phrase.key -> checked
        self._group_of = None                   # callable(phrase)->str
        self._code_display = None               # callable(phrase)->display code
        self._headers = ["选择", "文本", "编码", "权重", "分组", "操作"]

    # ------------------------------------------------------------------ #
    def set_phrases(self, phrases: list[Phrase], group_of=None,
                    selected_of=None, code_display=None) -> None:
        """内容变化时重建（beginResetModel）。"""
        self.beginResetModel()
        self._phrases = list(phrases)
        self._group_of = group_of
        self._code_display = code_display
        self._checked = {
            p.key: bool(selected_of(p)) if selected_of else False
            for p in phrases
        }
        self.endResetModel()

    def reorder(self, ordered: list[Phrase]) -> None:
        """仅重排显示顺序（排序变化）。不重建内容，零控件开销。"""
        self._phrases = list(ordered)
        self.layoutChanged.emit()

    # ------------------------------------------------------------------ #
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._phrases)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else 6

    def headerData(self, section: int, orientation: Qt.Orientation, role: int):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        col = index.column()
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if col in (COL_TEXT, COL_CODE, COL_WEIGHT):
            f |= Qt.ItemFlag.ItemIsEditable
        if col == COL_SELECT:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index: QModelIndex, role: int):
        if not index.isValid():
            return None
        col = index.column()
        row = index.row()
        if row >= len(self._phrases):
            return None
        p = self._phrases[row]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col == COL_TEXT:
                return p.text
            if col == COL_CODE:
                return self._code_display(p) if self._code_display else p.code
            if col == COL_WEIGHT:
                return str(p.weight)
            if col == COL_GROUP:
                g = self._group_of(p) if self._group_of else ""
                return g or UNGROUPED
            return None
        if role == Qt.ItemDataRole.CheckStateRole and col == COL_SELECT:
            return Qt.CheckState.Checked if self._checked.get(p.key, False) else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.BackgroundRole and p.is_conflict:
            return conflict_background()
        return None

    def setData(self, index: QModelIndex, value, role: int) -> bool:
        if not index.isValid():
            return False
        col = index.column()
        row = index.row()
        if row >= len(self._phrases):
            return False
        p = self._phrases[row]
        if col == COL_SELECT and role == Qt.ItemDataRole.CheckStateRole:
            checked = (value == Qt.CheckState.Checked)
            self._checked[p.key] = checked
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.selectionChanged.emit(p.key, checked)
            return True
        if col in (COL_TEXT, COL_CODE, COL_WEIGHT) and role == Qt.ItemDataRole.EditRole:
            self.cellEdited.emit(row, col, str(value))
            return True
        if col == COL_GROUP and role == Qt.ItemDataRole.EditRole:
            self.groupEdited.emit(row, str(value))
            return True
        return False

    # ------------------------------------------------------------------ #
    # 供 View 访问
    # ------------------------------------------------------------------ #
    def phrase_at_row(self, row: int) -> Phrase | None:
        if 0 <= row < len(self._phrases):
            return self._phrases[row]
        return None

    def key_at_row(self, row: int) -> str:
        p = self.phrase_at_row(row)
        return p.key if p else ""

    def get_checked_keys(self) -> list[str]:
        return [k for k, v in self._checked.items() if v]

    def set_all_checked(self, state: bool) -> None:
        for k in self._checked:
            self._checked[k] = state
        last = self.rowCount() - 1
        if last >= 0:
            self.dataChanged.emit(
                self.index(0, COL_SELECT), self.index(last, COL_SELECT),
                [Qt.ItemDataRole.CheckStateRole])

    def update_group_cell(self, row: int, text: str) -> None:
        if 0 <= row < self.rowCount():
            self.dataChanged.emit(
                self.index(row, COL_GROUP), self.index(row, COL_GROUP))


class ButtonDelegate(QStyledItemDelegate):
    """操作列「删除」按钮委托：paint 绘制 + 命中测试点击。"""

    deleteRequested = Signal(int)  # row

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)

    def paint(self, painter, option, index) -> None:
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        btn = self._button_rect(option.rect)
        style = QApplication.style()
        opt = QStyleOptionButton()
        opt.rect = btn
        opt.text = "删除"
        opt.state = QStyle.StateFlag.State_Enabled
        style.drawControl(QStyle.ControlElement.CE_PushButton, opt, painter)

    def editorEvent(self, event, model, option, index) -> bool:
        if event.type() == QEvent.Type.MouseButtonRelease:
            if self._button_rect(option.rect).contains(event.pos()):
                self.deleteRequested.emit(index.row())
                return True
        return False

    @staticmethod
    def _button_rect(rect: QRect) -> QRect:
        w = min(72, rect.width() - 8)
        h = min(24, rect.height() - 6)
        x = rect.x() + (rect.width() - w) // 2
        y = rect.y() + (rect.height() - h) // 2
        return QRect(x, y, w, h)
