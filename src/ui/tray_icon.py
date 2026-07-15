"""System tray integration for the Rime configuration tool."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget


class TrayIcon(QSystemTrayIcon):
    """Persistent tray menu with a Windows activation-event fallback."""

    requestOpen = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trigger_timer = QTimer(self)
        self._trigger_timer.setSingleShot(True)
        self._trigger_timer.setInterval(260)
        self._trigger_timer.timeout.connect(self.requestOpen.emit)
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()
        self._act_open = menu.addAction("打开主窗口")
        self._act_deploy = menu.addAction("立即重新部署")
        self._act_auto_deploy = menu.addAction("保存后自动部署")
        self._act_auto_deploy.setCheckable(True)
        self._act_hotkey = menu.addAction("热键收藏：开")
        self._act_hotkey.setCheckable(True)
        self._act_hotkey.setChecked(True)
        menu.addSeparator()
        self._act_settings = menu.addAction("设置")
        menu.addSeparator()
        self._act_quit = menu.addAction("退出")
        self.setContextMenu(menu)
        self.setToolTip("RIME 配置小工具")

    def set_hotkey_state(self, enabled: bool) -> None:
        self._act_hotkey.setText("热键收藏：开" if enabled else "热键收藏：关")
        self._act_hotkey.setChecked(enabled)

    def set_icon(self, icon) -> None:
        self.setIcon(icon)

    def _on_activated(self, reason) -> None:
        activation = QSystemTrayIcon.ActivationReason
        if reason == activation.DoubleClick:
            self._trigger_timer.stop()
            self.requestOpen.emit()
        elif reason == activation.Trigger:
            # Some Windows shell configurations never emit DoubleClick. Delay
            # the single-click fallback so a subsequent DoubleClick wins.
            self._trigger_timer.start()

    @property
    def action_open(self):
        return self._act_open

    @property
    def action_deploy(self):
        return self._act_deploy

    @property
    def action_auto_deploy(self):
        return self._act_auto_deploy

    @property
    def action_hotkey(self):
        return self._act_hotkey

    @property
    def action_settings(self):
        return self._act_settings

    @property
    def action_quit(self):
        return self._act_quit

    def set_auto_deploy_state(self, enabled: bool) -> None:
        self._act_auto_deploy.setChecked(enabled)
