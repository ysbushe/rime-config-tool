"""Stable sidebar navigation shell used by the VNext main window."""
from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QStyle,
)


class NavigationShell(QWidget):
    """Sidebar + stack with the small tab-like API used by MainWindow.

    Keeping this compatibility boundary lets page business code remain stable
    while the application-level information architecture changes.
    """

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Central")
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rail = QFrame(self)
        rail.setObjectName("NavigationRail")
        rail.setFixedWidth(204)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(14, 18, 14, 14)
        rail_layout.setSpacing(10)
        product = QLabel("RIME 配置工具", rail)
        product.setObjectName("NavigationTitle")
        product_note = QLabel("词库与输入方案工作台", rail)
        product_note.setObjectName("NavigationProductNote")
        section = QLabel("工作区", rail)
        section.setObjectName("NavigationSection")
        self._sidebar = QListWidget(rail)
        self._sidebar.setObjectName("Sidebar")
        self._sidebar.setSpacing(3)
        self._sidebar.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        rail_layout.addWidget(product)
        rail_layout.addWidget(product_note)
        rail_layout.addSpacing(12)
        rail_layout.addWidget(section)
        rail_layout.addWidget(self._sidebar, 1)

        self._connection_status = QLabel("●  正在检查 Rime", rail)
        self._connection_status.setObjectName("NavigationStatus")
        self._connection_status.setProperty("state", "checking")
        self._connection_status.setWordWrap(True)
        rail_layout.addWidget(self._connection_status)

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("WorkspaceStack")
        root.addWidget(rail)
        root.addWidget(self._stack, 1)

        self._sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._stack.currentChanged.connect(self._sync_current_index)

    def addTab(self, widget: QWidget, title: str) -> int:  # noqa: N802
        return self.insertTab(self.count(), widget, title)

    def insertTab(self, index: int, widget: QWidget, title: str) -> int:  # noqa: N802
        bounded = max(0, min(index, self.count()))
        item = QListWidgetItem(self._navigation_icon(title), title)
        item.setToolTip(title)
        self._sidebar.insertItem(bounded, item)
        result = self._stack.insertWidget(bounded, widget)
        if self._stack.currentIndex() < 0:
            self.setCurrentIndex(0)
        return result

    def removeTab(self, index: int) -> None:  # noqa: N802
        widget = self._stack.widget(index)
        if widget is None:
            return
        self._stack.removeWidget(widget)
        self._sidebar.takeItem(index)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if 0 <= index < self.count():
            self._stack.setCurrentIndex(index)
            if self._sidebar.currentRow() != index:
                self._sidebar.setCurrentRow(index)

    def currentIndex(self) -> int:  # noqa: N802
        return self._stack.currentIndex()

    def count(self) -> int:
        return self._stack.count()

    def widget(self, index: int) -> QWidget | None:
        return self._stack.widget(index)

    def set_connection_status(self, text: str, state: str = "ready") -> None:
        """Update the persistent environment status without changing pages."""
        self._connection_status.setText(text)
        self._connection_status.setProperty("state", state)
        self._connection_status.style().unpolish(self._connection_status)
        self._connection_status.style().polish(self._connection_status)

    def _navigation_icon(self, title: str):
        style = self.style()
        if "词典" in title:
            icon = QStyle.StandardPixmap.SP_DirOpenIcon
        elif "符号" in title:
            icon = QStyle.StandardPixmap.SP_FileDialogContentsView
        elif "设置" in title:
            icon = QStyle.StandardPixmap.SP_FileDialogDetailedView
        else:
            icon = QStyle.StandardPixmap.SP_FileIcon
        return style.standardIcon(icon)

    def _sync_current_index(self, index: int) -> None:
        if self._sidebar.currentRow() != index:
            self._sidebar.setCurrentRow(index)
        self.currentChanged.emit(index)
