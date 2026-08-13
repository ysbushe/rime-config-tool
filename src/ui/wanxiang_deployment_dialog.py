"""Application-modal progress shown while a Wanxiang deployment is disruptive."""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout, QWidget


class WanxiangDeploymentDialog(QDialog):
    """Keep the deployment interruption visible without blocking the GUI thread."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("正在重新部署万象")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(470)
        self._active = False
        self._elapsed = QElapsedTimer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("正在重新部署相关文件")
        title.setObjectName("DialogSectionTitle")
        layout.addWidget(title)
        self._phase = QLabel()
        self._phase.setWordWrap(True)
        layout.addWidget(self._phase)
        detail = QLabel(
            "重新部署期间中文输入暂不可用，各应用的输入窗口也可能短暂无响应。"
            "请勿退出本工具或重复触发部署。"
        )
        detail.setWordWrap(True)
        detail.setProperty("role", "muted")
        layout.addWidget(detail)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._progress)
        self._time = QLabel("预计约 1～2 分钟")
        self._time.setProperty("role", "muted")
        layout.addWidget(self._time)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_elapsed)

    @property
    def active(self) -> bool:
        return self._active

    def start(self, phase: str = "正在准备重新部署…") -> None:
        if not self._active:
            self._elapsed.start()
        self._active = True
        self._phase.setText(phase)
        self._update_elapsed()
        self._timer.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def set_phase(self, phase: str) -> None:
        self._phase.setText(phase)

    def finish(self) -> None:
        self._active = False
        self._timer.stop()
        self.hide()

    def reject(self) -> None:
        if not self._active:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._active:
            event.ignore()
            return
        super().closeEvent(event)

    def _update_elapsed(self) -> None:
        elapsed_seconds = max(0, self._elapsed.elapsed() // 1000)
        minutes, seconds = divmod(elapsed_seconds, 60)
        self._time.setText(
            f"预计约 1～2 分钟 · 已用时 {minutes:02d}:{seconds:02d}"
        )
