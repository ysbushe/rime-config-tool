"""系统托盘（TrayIcon）。

基于 PySide6 内置 QSystemTrayIcon，常驻菜单：
    打开主窗口 / 立即重新部署 / 保存后自动部署 / 热键收藏 / 设置 / 退出
（不引入 pystray，遵循既定技术栈决策。）
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标与菜单。"""

    requestOpen = Signal()  # 双击托盘图标 → 打开主窗口

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_menu()
        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    def set_hotkey_state(self, enabled: bool) -> None:
        self._act_hotkey.setText("热键收藏：开" if enabled else "热键收藏：关")
        self._act_hotkey.setChecked(enabled)

    def set_icon(self, icon) -> None:
        self.setIcon(icon)

    # ------------------------------------------------------------------ #
    def _on_activated(self, reason) -> None:
        # 双击托盘图标 → 打开主窗口（单左键仅弹右键菜单，不动作）
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.requestOpen.emit()

    # 信号连接由主窗口负责，这里仅暴露动作对象
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
