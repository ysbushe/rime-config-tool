"""Focused history rollback and deployment status dialogs."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)


class HistoryDialog(QDialog):
    """Audit history with rollback exposed only for records with snapshots."""

    def __init__(self, history: list[dict], rollback_callback, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("修改历史与回滚")
        self.resize(900, 520)
        self._history = list(history)
        self._rollback_callback = rollback_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        note = QLabel(
            "仅带有完整写前备份的记录可以回滚。回滚前会再次备份当前文件，"
            "旧版本记录仍可查看，但不会冒险恢复。",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("role", "info")
        layout.addWidget(note)
        table = QTableWidget(len(self._history), 4, self)
        table.setHorizontalHeaderLabels(["时间", "操作", "说明", "恢复"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for row, item in enumerate(self._history):
            for column, value in enumerate((
                str(item.get("at", "")), str(item.get("action", "")),
                str(item.get("detail", "")),
            )):
                table.setItem(row, column, QTableWidgetItem(value))
            backups = item.get("backups")
            button = QPushButton("回滚到此前", table)
            button.setEnabled(isinstance(backups, dict) and bool(backups))
            button.setToolTip(
                "恢复此操作发生前的词库文件" if button.isEnabled()
                else "这条旧记录没有可验证的备份快照"
            )
            button.clicked.connect(
                lambda _=False, selected=item: self._rollback(selected)
            )
            table.setCellWidget(row, 3, button)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _rollback(self, item: dict) -> None:
        if self._rollback_callback(item):
            self.accept()


class DeploymentStatusDialog(QDialog):
    """Small live status center for the serialized deployment worker."""

    def __init__(self, state_provider, history_provider, deploy_callback,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("部署状态")
        self.resize(760, 460)
        self._state_provider = state_provider
        self._history_provider = history_provider
        self._deploy_callback = deploy_callback
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self._state = QLabel(self)
        self._state.setWordWrap(True)
        self._state.setProperty("role", "info")
        layout.addWidget(self._state)
        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["时间", "方式", "结果"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table, 1)
        actions = QHBoxLayout()
        deploy = QPushButton("立即重新部署", self)
        deploy.setObjectName("Primary")
        deploy.clicked.connect(self._request_deploy)
        close = QPushButton("关闭", self)
        close.clicked.connect(self.accept)
        actions.addWidget(deploy)
        actions.addStretch(1)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def _request_deploy(self) -> None:
        self._deploy_callback()
        self.refresh()

    def refresh(self) -> None:
        state = dict(self._state_provider())
        if state.get("running"):
            message = "正在后台部署最新修改。"
            if state.get("pending"):
                message += " 已合并新的部署请求，当前任务结束后会再执行一次。"
        elif not state.get("available"):
            message = "未找到小狼毫部署程序；请先在设置页确认部署路径。"
        else:
            message = "当前没有部署任务。需要时可在这里手动重新部署。"
        self._state.setText(message)
        history = [
            item for item in self._history_provider()
            if "部署" in str(item.get("action", ""))
        ][:30]
        self._table.setRowCount(len(history))
        for row, item in enumerate(history):
            for column, value in enumerate((
                str(item.get("at", "")), str(item.get("action", "")),
                str(item.get("detail", "")),
            )):
                self._table.setItem(row, column, QTableWidgetItem(value))
