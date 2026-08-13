"""Entry point for the on-demand input diagnostics companion."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from src import __version__
from src.plugins.input_diagnostics import InputDiagnosticsService
from src.settings import Settings
from src.ui.input_diagnostics_widget import InputDiagnosticsWidget
from src.ui.theme import apply_theme, apply_window_theme


class InputDiagnosticsWindow(QMainWindow):
    """A process-isolated diagnostics window."""

    def __init__(self) -> None:
        super().__init__()
        self._service = InputDiagnosticsService()
        testing = " · GUI测试中" if os.environ.get("RIME_CONFIG_GUI_TEST") == "1" else ""
        self.setWindowTitle(f"RIME 输入诊断 v{__version__}{testing}")
        self.resize(880, 620)
        self.setCentralWidget(InputDiagnosticsWidget(self._service, self))
        theme = Settings().theme
        apply_theme(theme)
        apply_window_theme(self, theme)
        icon_path = Path(__file__).resolve().parents[1] / "assets" / "app.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        QTimer.singleShot(0, self._service.start)

    def closeEvent(self, event) -> None:
        self._service.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RIME 输入诊断")
    app.setQuitOnLastWindowClosed(True)
    window = InputDiagnosticsWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
