"""分组侧栏（GroupPanel）。

词库管理页左侧分组导航（方案 A：sidecar 元数据）。
信号：groupSelected(name) —— name 为『全部』或具体分组名。

视觉：
    - 真正的区块标题（QLabel#BlockTitle，非禁用主按钮模拟）；
    - 白色背景改为透明（继承主题底色），选中态用色彩条 + 底色叠加 + 加粗 + 着色文字，
      不只依赖左侧色条；深色主题下无内联白底；
    - 『全部』状态下删除按钮禁用；
    - 长分组名省略并显示完整工具提示。
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QLabel,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.service.group_service import GroupService
from src.ui.delete_confirm_dialog import confirm_delete
from src.ui.theme import accent_color

# 分组配色（循环取色，保证每组有稳定可辨识的颜色）
_GROUP_COLORS = ["#185FA5", "#D4380D", "#389E0D", "#722ED1",
                 "#D48806", "#08979C", "#C41D7F", "#595959"]

# 普通分组按钮样式（左侧色条 + 透明背景 + 选中叠加 + 着色文字）
# 用 rgba 叠加避免硬编码主题白底，深色主题下同样成立。
_STYLE_GROUP = (
    "QPushButton{{text-align:left; padding-left:12px; padding-right:10px;"
    " border:none; border-left:5px solid {color}; background:transparent;}}"
    "QPushButton:checked{{background:{overlay}; color:{color};"
    " font-weight:bold; border-left:5px solid {color};}}"
    "QPushButton:disabled{{color:#9AA0A6; border-left:5px solid #3A3F47;}}"
)
# “全部”按钮样式（中性灰条 + 选中主色描边/底色；主色取自 @ACCENT@ token）
_STYLE_ALL = (
    "QPushButton{{text-align:left; padding-left:12px; padding-right:10px;"
    " border:none; border-left:5px solid #BBBBBB; background:transparent;}}"
    "QPushButton:checked{{background:{overlay}; color:{accent};"
    " font-weight:bold; border-left:5px solid {accent};}}"
)


def _rgba(hex_color: str, alpha: float) -> str:
    """将 #RRGGBB 转为 rgba(r,g,b,a) 字符串（Qt QSS 支持）。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _elide(text: str, max_chars: int = 12) -> str:
    """超长分组名省略（保留完整内容在 toolTip）。"""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


class GroupPanel(QWidget):
    """分组侧栏。"""

    groupSelected = Signal(str)

    def __init__(self, group_service: GroupService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups = group_service
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("分组")
        title.setObjectName("BlockTitle")
        layout.addWidget(title)

        # 用按钮列表实现（便于样式化选中态）
        self._btn_all = QPushButton(GroupService.all_group_label())
        self._btn_all.setCheckable(True)
        accent = accent_color()
        self._btn_all.setStyleSheet(
            _STYLE_ALL.format(overlay=_rgba(accent, 0.10), accent=accent))
        self._btn_all.clicked.connect(lambda: self._select(GroupService.all_group_label()))
        layout.addWidget(self._btn_all)

        self._group_buttons: dict[str, QPushButton] = {}

        actions = QHBoxLayout()
        self._btn_add = QPushButton("新建分组")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_del = QPushButton("删除分组")
        self._btn_del.setObjectName("Danger")
        self._btn_del.clicked.connect(self._on_delete)
        actions.addWidget(self._btn_add)
        actions.addWidget(self._btn_del)
        layout.addLayout(actions)

        layout.addStretch(1)
        self._current = GroupService.all_group_label()

    # ------------------------------------------------------------------ #
    def restyle(self) -> None:
        """主题切换后重设『全部』按钮主色（取当前 @ACCENT@ token）。

        分组按钮的数据色（_GROUP_COLORS 8 色循环）保持不变。
        """
        accent = accent_color()
        self._btn_all.setStyleSheet(
            _STYLE_ALL.format(overlay=_rgba(accent, 0.10), accent=accent))

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        # 主题切换后『全部』按钮主色需随 @ACCENT@ 更新
        self.restyle()
        # 移除旧分组按钮（保留『全部』）
        for btn in self._group_buttons.values():
            btn.deleteLater()
        self._group_buttons.clear()

        layout = self.layout()
        fm = QFontMetrics(self.font())
        for idx, name in enumerate(self._groups.list_groups()):
            color = _GROUP_COLORS[idx % len(_GROUP_COLORS)]
            btn = QPushButton(_elide(name))
            btn.setToolTip(name)  # 完整名称
            btn.setCheckable(True)
            btn.setStyleSheet(_STYLE_GROUP.format(
                color=color, overlay=_rgba(color, 0.12)))
            btn.clicked.connect(lambda _=False, n=name: self._select(n))
            # 插入到 actions 之前：找到 actions 行索引
            layout.insertWidget(layout.indexOf(self._btn_add), btn)
            self._group_buttons[name] = btn

        self._apply_selection_style()

    # ------------------------------------------------------------------ #
    def _select(self, name: str) -> None:
        self._current = name
        self._apply_selection_style()
        self.groupSelected.emit(name)

    def _apply_selection_style(self) -> None:
        self._btn_all.setChecked(self._current == GroupService.all_group_label())
        for n, btn in self._group_buttons.items():
            btn.setChecked(n == self._current)
        # 『全部』状态下删除按钮禁用
        self._btn_del.setEnabled(self._current != GroupService.all_group_label())

    def _on_add(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建分组", "分组名称：")
        if ok and name.strip():
            if self._groups.add_group(name.strip()):
                self.refresh()
                self._select(name.strip())

    def _on_delete(self) -> None:
        if self._current in (GroupService.all_group_label(), ""):
            return
        if not confirm_delete(
            self,
            "删除分组",
            f"确认删除分组「{self._current}」？成员将变为未分组。",
        ):
            return
        self._groups.remove_group(self._current)
        self.refresh()
        self._select(GroupService.all_group_label())

    def select_all(self, emit: bool = True) -> None:
        name = GroupService.all_group_label()
        self._current = name
        self._apply_selection_style()
        if emit:
            self.groupSelected.emit(name)

    def current_group(self) -> str:
        return self._current
