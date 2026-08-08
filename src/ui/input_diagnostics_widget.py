"""User interface for the privacy-safe input diagnostics plug-in."""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from src.plugins.input_diagnostics import InputDiagnosticsService


class InputDiagnosticsWidget(QWidget):
    def __init__(self, service: InputDiagnosticsService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        layout = QVBoxLayout(self)
        title = QLabel("输入法运行诊断")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel(
            "仅记录进程名、窗口类/位置、前台进程和 Ctrl/Alt/Shift/Win 状态；"
            "不记录窗口标题、按键内容、剪贴板或 Rime 组词内容。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        status_box = QGroupBox("当前状态")
        grid = QGridLayout(status_box)
        self._monitoring = QLabel()
        self._processes = QLabel("—")
        self._modifiers = QLabel("无")
        self._windows = QLabel("0")
        for row, (name, value) in enumerate((
            ("监控", self._monitoring), ("相关进程", self._processes),
            ("修饰键", self._modifiers), ("可疑弹窗", self._windows),
        )):
            grid.addWidget(QLabel(name + "："), row, 0)
            grid.addWidget(value, row, 1)
        layout.addWidget(status_box)

        buttons = QHBoxLayout()
        self._btn_toggle = QPushButton()
        self._btn_capture = QPushButton("记录当前现场")
        self._btn_copy = QPushButton("复制诊断报告")
        self._btn_folder = QPushButton("打开日志目录")
        for button in (self._btn_toggle, self._btn_capture, self._btn_copy, self._btn_folder):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._events = QPlainTextEdit()
        self._events.setReadOnly(True)
        self._events.setPlaceholderText("异常窗口出现或消失时，事件会显示在这里。")
        layout.addWidget(self._events, 1)

        self._btn_toggle.clicked.connect(self._toggle)
        self._btn_capture.clicked.connect(lambda: self._service.capture_now("ui_button"))
        self._btn_copy.clicked.connect(self.copy_report)
        self._btn_folder.clicked.connect(self.open_log_folder)
        self._service.updated.connect(self.refresh)
        self._service.monitoringChanged.connect(self._set_monitoring)
        self._set_monitoring(self._service.monitoring)
        self.refresh(getattr(self._service, "_last_state", {}))

    def _toggle(self) -> None:
        self._service.stop() if self._service.monitoring else self._service.start()

    def _set_monitoring(self, enabled: bool) -> None:
        self._monitoring.setText("运行中" if enabled else "已停止")
        self._btn_toggle.setText("停止监控" if enabled else "开始监控")

    def refresh(self, state: dict) -> None:
        processes = sorted({item.get("process", "") for item in state.get("processes", []) if item.get("process")})
        self._processes.setText("、".join(processes) or "未检测到")
        active = [key.upper() for key, down in state.get("modifiers", {}).items() if down]
        self._modifiers.setText(" + ".join(active) or "无")
        self._windows.setText(str(len(state.get("windows", []))))
        lines = []
        for event in self._service.recent_events()[-80:]:
            details = json.dumps(event["details"], ensure_ascii=False, separators=(",", ":"))
            lines.append(f'{event["at"]}  {event["event"]}  {details}')
        self._events.setPlainText("\n".join(lines))
        self._events.verticalScrollBar().setValue(self._events.verticalScrollBar().maximum())

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self._service.recent_report())
        self._btn_copy.setText("已复制")

    def open_log_folder(self) -> None:
        folder = Path(self._service.log_path).parent
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(folder)))