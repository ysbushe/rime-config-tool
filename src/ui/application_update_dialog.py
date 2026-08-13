"""Application-modal progress shown while the tool prepares its own update."""
from __future__ import annotations

import os

from PySide6.QtCore import QElapsedTimer, QTimer, Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout, QWidget

from src import __version__


class ApplicationUpdateDialog(QDialog):
    """Keep application update progress visible until handoff or failure."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        testing = f" · v{__version__} · GUI测试中" \
            if os.environ.get("RIME_CONFIG_GUI_TEST") == "1" else ""
        self.setWindowTitle(f"正在更新 RIME 配置小工具{testing}")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(470)
        self._active = False
        self._elapsed = QElapsedTimer()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("正在准备程序更新")
        title.setObjectName("DialogSectionTitle")
        layout.addWidget(title)
        self._phase = QLabel()
        self._phase.setWordWrap(True)
        layout.addWidget(self._phase)
        detail = QLabel(
            "更新完成后程序将自动关闭并重新启动，请勿重复启动程序或移动安装目录。"
        )
        detail.setWordWrap(True)
        detail.setProperty("role", "muted")
        layout.addWidget(detail)
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        layout.addWidget(progress)
        self._time = QLabel()
        self._time.setProperty("role", "muted")
        layout.addWidget(self._time)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._update_elapsed)

    @property
    def active(self) -> bool:
        return self._active

    def start(self, phase: str) -> None:
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
            f"预计约 1～3 分钟 · 已用时 {minutes:02d}:{seconds:02d}"
        )
