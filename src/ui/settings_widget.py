"""设置页（SettingsWidget）。

包含：
    - Rime 目录与部署（合并为一组：目录 + 部署器路径 + 立即部署 + 重新探测）
    - 方案信息与本程序受管文件状态
    - 行为：自动部署 / 全局热键（键盘捕获组合）/ 备份保留份数（下拉 1~5 + 自定义≤10）
      / 沙盒模式 / 主题 / 版本
    - 开机自启
变更即时写入 Settings（记忆 JSON）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src import __version__
from src.config.paths import (
    PHRASE_FILENAME,
    SCHEMA_FILENAME,
    SYMBOLS_FILENAME,
    user_config_dir,
)
from src.repo.schema_repo import SchemaRepo
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.visible_checkbox import VisibleCheckBox
from src.ui.click_activated_spin import ClickActivatedSpinBox
from src.settings import Settings
from src.service.autostart import Autostart
from src.service.deploy_service import DeployService
from src.service.backup_service import BackupService
from src.ui.theme import THEME_TOKENS
from src.service.update_service import REPOSITORY_URL, UpdateService


_MANAGED_FILES = (
    (PHRASE_FILENAME, "词库短语"),
    (SCHEMA_FILENAME, "输入方案"),
    (SYMBOLS_FILENAME, "符号表"),
)

_THEME_OPTIONS = (
    ("light", "浅色(A)", "明亮、清晰，适合日常维护"),
    ("dark", "深色(B)", "低亮度环境下更舒适"),
    ("ink", "水墨(C)", "暖纸底色、黛青主色"),
)


class ThemeOptionCard(QFrame):
    """设置页主题卡片：点击即发出内部主题值。"""

    clicked = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self.setObjectName("ThemeCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(92)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("ThemeCardTitle")
        self._check = QLabel("")
        self._check.setObjectName("ThemeCardCheck")
        self._check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._check.setFixedSize(20, 20)
        title_row.addWidget(title_label, 1)
        title_row.addWidget(self._check)
        root.addLayout(title_row)

        preview = QHBoxLayout()
        preview.setSpacing(4)
        tokens = THEME_TOKENS[key]
        for token in ("@BG_SIDEBAR@", "@BG_SURFACE@", "@ACCENT@"):
            swatch = QFrame()
            swatch.setObjectName("ThemeSwatch")
            swatch.setFixedSize(30, 16)
            swatch.setStyleSheet(
                "QFrame#ThemeSwatch{"
                f"background:{tokens[token]};"
                f"border:1px solid {tokens['@BORDER_STRONG@']};"
                "border-radius:4px;"
                "}"
            )
            preview.addWidget(swatch)
        preview.addStretch(1)
        root.addLayout(preview)

        desc = QLabel(description)
        desc.setObjectName("ThemeCardDesc")
        desc.setWordWrap(True)
        root.addWidget(desc)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._check.setText("✓" if selected else "")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._key)
            event.accept()
            return
        super().keyPressEvent(event)


class SettingsWidget(QWidget):
    """设置界面。"""

    # 通知主窗口：Rime 目录变化 / 热键开关变化 / 沙盒模式变化 / 热键组合变化 / 主题变化
    rimeDirChanged = Signal(str)
    hotkeyToggled = Signal(bool)
    sandboxToggled = Signal(bool)
    hotkeyComboChanged = Signal(str)
    themeChanged = Signal(str)

    def __init__(self, settings: Settings, autostart: Autostart,
                 deploy: DeployService, schema_repo: SchemaRepo | None = None,
                 backup: BackupService | None = None,
                 health_opener=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._autostart = autostart
        self._deploy = deploy
        self._schema_repo = schema_repo
        self._health_opener = health_opener
        self._backup = backup or BackupService(
            settings.rime_dir, getattr(settings, "backup_count", 5),
            getattr(settings, "backup_dir", ""))
        self._managed_labels: dict[str, QLabel] = {}
        self._managed_open: dict[str, QPushButton] = {}
        self._extension_labels: dict[str, QLabel] = {}
        self._extension_open: dict[str, QPushButton] = {}
        self._extension_paths: dict[str, Path] = {}
        self._backup_versions_signature: tuple[str, str, int] | None = None
        self._update_results: queue.SimpleQueue[tuple[bool, str, bool]] = queue.SimpleQueue()
        self._update_poll = QTimer(self)
        self._update_poll.setInterval(100)
        self._update_poll.timeout.connect(self._drain_update_results)
        self._build_ui()
        self.refresh()

    def _on_library_health(self) -> None:
        if self._health_opener is not None:
            self._health_opener()

    def _build_theme_selector(self) -> QWidget:
        selector = QWidget()
        selector.setObjectName("ThemeSelector")
        row = QHBoxLayout(selector)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for key, title, description in _THEME_OPTIONS:
            card = ThemeOptionCard(key, title, description, selector)
            card.clicked.connect(self._on_theme_selected)
            self._theme_cards[key] = card
            row.addWidget(card)
        return selector

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        content = QWidget()
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        root = QVBoxLayout(content)
        root.setSpacing(12)

        # Rime 目录 + 部署（合并为一组：两者同属 Rime 部署功能）
        g_rime = QGroupBox("Rime 目录与部署")
        rime_form = QFormLayout(g_rime)

        rime_row = QHBoxLayout()
        self._rime_dir = QLineEdit()
        self._btn_browse = QPushButton("浏览…")
        self._btn_browse.clicked.connect(self._on_browse)
        self._btn_detect = QPushButton("重新探测")
        self._btn_detect.clicked.connect(self._on_detect)
        rime_row.addWidget(self._rime_dir, 1)
        rime_row.addWidget(self._btn_browse)
        rime_row.addWidget(self._btn_detect)
        rime_form.addRow("目录：", rime_row)

        # 部署器路径（自动探测后自动回填）
        dp_row = QHBoxLayout()
        self._deploy_path = QLineEdit()
        self._deploy_path.setPlaceholderText("未指定则自动探测 WeaselDeployer.exe")
        self._btn_deploy_browse = QPushButton("浏览…")
        self._btn_deploy_browse.clicked.connect(self._on_deploy_browse)
        self._deploy_path.editingFinished.connect(self._on_deploy_path_changed)
        dp_row.addWidget(self._deploy_path, 1)
        dp_row.addWidget(self._btn_deploy_browse)
        rime_form.addRow("部署器路径：", dp_row)

        self._lbl_deploy = QLabel()
        rime_form.addRow(self._lbl_deploy)

        self._btn_deploy = QPushButton("立即部署")
        self._btn_deploy.setObjectName("Primary")
        self._btn_deploy.clicked.connect(self._on_deploy)
        rime_form.addRow(self._btn_deploy)
        root.addWidget(g_rime)

        # 方案信息（只读）
        g_schema = QGroupBox("方案信息（只读）")
        schema_form = QFormLayout(g_schema)
        self._lbl_schema_name = QLabel()
        self._lbl_schema_ver = QLabel()
        self._lbl_schema_id = QLabel()
        schema_form.addRow("名称：", self._lbl_schema_name)
        schema_form.addRow("版本：", self._lbl_schema_ver)
        schema_form.addRow("ID：", self._lbl_schema_id)

        # 受管文件
        g_files = QGroupBox("受管文件")
        files_form = QFormLayout(g_files)
        for filename, purpose in _MANAGED_FILES:
            label = QLabel()
            label.setWordWrap(True)
            label.setProperty("role", "info")
            self._managed_labels[filename] = label
            open_button = QPushButton("打开")
            open_button.setFixedWidth(58)
            open_button.setEnabled(False)
            open_button.clicked.connect(lambda _=False, name=filename: self._open_managed_file(name))
            self._managed_open[filename] = open_button
            row = QHBoxLayout()
            row.addWidget(label, 1)
            row.addWidget(open_button)
            files_form.addRow(f"{purpose}：", row)

        g_extensions = QGroupBox("扩展词库（只读检测）")
        extensions_form = QFormLayout(g_extensions)
        for key, title in (
            ("english", "英文输入文件"),
            ("mixed", "中英混合用户词库"),
            ("display", "显示边界"),
        ):
            label = QLabel()
            label.setWordWrap(True)
            label.setProperty("role", "info")
            self._extension_labels[key] = label
            open_button = QPushButton("打开")
            open_button.setFixedWidth(58)
            open_button.setEnabled(False)
            open_button.clicked.connect(lambda _=False, name=key: self._open_extension_file(name))
            self._extension_open[key] = open_button
            row = QHBoxLayout()
            row.addWidget(label, 1)
            row.addWidget(open_button)
            extensions_form.addRow(f"{title}：", row)


        # 行为
        g_behavior = QGroupBox("行为")
        b_form = QFormLayout(g_behavior)
        self._cb_autodeploy = VisibleCheckBox("写文件后自动重新部署")
        self._cb_hotkey = VisibleCheckBox("启用全局热键收藏")
        # #8 热键组合：键盘捕获（按下组合键即暂存，点『应用热键』才重注册）
        self._hotkey_seq = QKeySequenceEdit()
        hotkey_btn_row = QHBoxLayout()
        self._btn_hotkey_apply = QPushButton("应用热键")
        self._btn_hotkey_apply.setObjectName("Primary")
        self._btn_hotkey_apply.clicked.connect(self._on_hotkey_apply)
        self._btn_hotkey_cancel = QPushButton("取消")
        self._btn_hotkey_cancel.clicked.connect(self._on_hotkey_cancel)
        hotkey_btn_row.addWidget(self._btn_hotkey_apply)
        hotkey_btn_row.addWidget(self._btn_hotkey_cancel)
        hotkey_btn_row.addStretch(1)
        self._lbl_hotkey_status = QLabel()
        self._lbl_hotkey_status.setWordWrap(True)
        self._lbl_hotkey_status.hide()
        self._pending_backup_settings: dict[str, object] = {}
        self._backup_combo = ClickActivatedComboBox()
        self._backup_combo.addItems([str(i) for i in range(1, 6)] + ["自定义…"])
        self._backup_combo.currentTextChanged.connect(self._on_backup_changed)
        self._cb_scheduled_backup = VisibleCheckBox("启用定期备份")
        self._backup_interval = ClickActivatedSpinBox()
        self._backup_interval.setRange(1, 365)
        self._backup_interval.setFixedWidth(88)
        self._cb_backup_cleanup = VisibleCheckBox("自动清理旧备份（保留最近份数）")
        self._btn_backup_settings_save = QPushButton("保存备份设置")
        self._btn_backup_settings_save.setObjectName("Primary")
        self._btn_backup_settings_save.clicked.connect(self._save_backup_settings)
        self._btn_backup_now = QPushButton("立即备份")
        self._btn_backup_now.setObjectName("Primary")
        self._cb_scheduled_backup.toggled.connect(self._on_scheduled_backup_toggled)
        self._backup_interval.valueChanged.connect(self._on_backup_interval_changed)
        self._cb_backup_cleanup.toggled.connect(self._on_backup_cleanup_toggled)
        self._btn_backup_now.clicked.connect(self._on_backup_now)
        self._theme_cards: dict[str, ThemeOptionCard] = {}
        self._theme_selector = self._build_theme_selector()
        self._dev_controls_visible = (not getattr(sys, "frozen", False)) or bool(os.environ.get("RIME_CONFIG_TOOL_DEV"))
        self._cb_sandbox = VisibleCheckBox("沙盒预览模式（不修改真实 Rime 配置，操作副本）")
        self._lbl_version = QLabel(__version__)
        self._lbl_version.setObjectName("MutedVersion")
        self._repo_link = QLabel(f'<a href="{REPOSITORY_URL}">GitHub 仓库</a>')
        self._repo_link.setOpenExternalLinks(True)
        self._btn_check_update = QPushButton("检查更新")
        self._btn_check_update.clicked.connect(self._on_check_update)
        self._btn_user_guide = QPushButton("用户说明")
        self._btn_user_guide.clicked.connect(lambda: self._open_project_document("docs/USER_GUIDE.md"))
        self._btn_release_notes = QPushButton("更新说明")
        self._btn_release_notes.clicked.connect(lambda: self._open_project_document("docs/RELEASE_NOTES.md"))
        self._lbl_update_status = QLabel()
        self._lbl_update_status.setWordWrap(True)
        self._lbl_update_status.setProperty("role", "info")
        self._lbl_update_status.setVisible(False)

        self._cb_autodeploy.toggled.connect(lambda v: self._settings.set("auto_deploy", v))
        self._cb_hotkey.toggled.connect(self._on_hotkey_toggled)
        self._cb_sandbox.toggled.connect(self._on_sandbox_toggled)

        b_form.addRow(self._cb_autodeploy)
        b_form.addRow("热键组合（按下组合键）：", self._hotkey_seq)
        b_form.addRow("", hotkey_btn_row)
        b_form.addRow("", self._lbl_hotkey_status)
        b_form.addRow(self._cb_hotkey)
        b_form.addRow("主题：", self._theme_selector)
        if self._dev_controls_visible:
            b_form.addRow(self._cb_sandbox)
        root.addWidget(g_behavior)

        g_library = QGroupBox("词库维护")
        library_layout = QHBoxLayout(g_library)
        self._btn_library_health = QPushButton("词库检查")
        self._btn_library_health.setObjectName("Primary")
        self._btn_library_health.setEnabled(self._health_opener is not None)
        self._btn_library_health.clicked.connect(self._on_library_health)
        library_layout.addWidget(QLabel("检查空值、权重、重复项和无效分组。"))
        library_layout.addStretch(1)
        library_layout.addWidget(self._btn_library_health)
        root.addWidget(g_library)

        g_github = QGroupBox("GitHub 更新")
        github_layout = QVBoxLayout(g_github)
        github_row = QHBoxLayout()
        github_row.addWidget(QLabel("版本："))
        github_row.addWidget(self._lbl_version)
        github_row.addSpacing(14)
        github_row.addWidget(self._repo_link)
        github_row.addWidget(self._btn_user_guide)
        github_row.addWidget(self._btn_release_notes)
        github_row.addStretch(1)
        github_row.addWidget(self._btn_check_update)
        github_layout.addLayout(github_row)
        github_layout.addWidget(self._lbl_update_status)
        root.addWidget(g_github)

        g_backup = QGroupBox("备份与恢复")
        backup_form = QFormLayout(g_backup)
        backup_path_row = QHBoxLayout()
        self._backup_path = QLineEdit()
        self._backup_path.setReadOnly(True)
        self._btn_backup_browse = QPushButton("自定义…")
        self._btn_backup_browse.clicked.connect(self._on_backup_browse)
        self._btn_backup_default = QPushButton("使用默认")
        self._btn_backup_default.clicked.connect(self._on_backup_default)
        self._btn_backup_open = QPushButton("打开备份文件夹")
        self._btn_backup_open.clicked.connect(self._open_backup_dir)
        self._lbl_next_backup = QLabel()
        self._lbl_next_backup.setProperty("role", "info")
        backup_path_row.addWidget(self._backup_path, 1)
        backup_path_row.addWidget(self._btn_backup_browse)
        backup_path_row.addWidget(self._btn_backup_default)
        backup_path_row.addWidget(self._btn_backup_open)
        backup_form.addRow("备份路径：", backup_path_row)

        self._restore_file = ClickActivatedComboBox()
        for filename, purpose in _MANAGED_FILES:
            self._restore_file.addItem(purpose, filename)
        self._restore_version = ClickActivatedComboBox()
        self._btn_restore = QPushButton("恢复所选备份")
        self._btn_restore.setObjectName("Primary")
        self._btn_restore.clicked.connect(self._on_restore_backup)
        self._restore_file.currentIndexChanged.connect(self._refresh_backup_versions)

        backup_settings = QWidget(g_backup)
        settings_grid = QGridLayout(backup_settings)
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(9)
        settings_grid.setVerticalSpacing(7)
        action_width = 112
        for button in (self._btn_backup_settings_save, self._btn_backup_now, self._btn_restore):
            button.setFixedWidth(action_width)

        settings_grid.addWidget(QLabel("保留份数："), 0, 0)
        settings_grid.addWidget(self._backup_combo, 0, 1)
        settings_grid.addWidget(self._cb_scheduled_backup, 0, 2)
        settings_grid.addWidget(self._cb_backup_cleanup, 0, 3)
        settings_grid.addWidget(self._btn_backup_settings_save, 0, 5)

        settings_grid.addWidget(QLabel("恢复："), 1, 0)
        settings_grid.addWidget(self._restore_file, 1, 1)
        settings_grid.addWidget(self._restore_version, 1, 2, 1, 2)
        settings_grid.addWidget(self._btn_backup_now, 1, 5)

        settings_grid.addWidget(QLabel("备份周期："), 2, 0)
        settings_grid.addWidget(self._backup_interval, 2, 1)
        settings_grid.addWidget(QLabel("天"), 2, 2)
        self._lbl_next_backup.setContentsMargins(20, 0, 0, 0)
        settings_grid.addWidget(self._lbl_next_backup, 2, 3, 1, 2)
        settings_grid.addWidget(self._btn_restore, 2, 5)
        settings_grid.setColumnStretch(3, 1)
        backup_form.addRow(backup_settings)

        self._lbl_backup_status = QLabel()
        self._lbl_backup_status.setWordWrap(True)
        self._lbl_backup_status.setProperty("role", "info")
        self._lbl_backup_status.setVisible(False)
        backup_form.addRow(self._lbl_backup_status)
        root.addWidget(g_backup)

        # 自启
        g_start = QGroupBox("开机自启")
        s_form = QFormLayout(g_start)
        self._cb_autostart = VisibleCheckBox("开机自动启动")
        self._cb_autostart.toggled.connect(self._on_autostart_toggled)
        s_form.addRow(self._cb_autostart)
        root.addWidget(g_start)

        # 状态信息置底：先受管文件，最后为只读方案信息。
        root.addWidget(g_files)
        root.addWidget(g_extensions)
        root.addWidget(g_schema)

        root.addStretch(1)
        self._scroll.setWidget(content)
        outer.addWidget(self._scroll)

    # ------------------------------------------------------------------ #
    def set_schema_repo(self, repo: SchemaRepo) -> None:
        self._schema_repo = repo
        self.refresh()

    def refresh(self) -> None:
        self._rime_dir.setText(self._settings.rime_dir)
        self._backup.keep = self._settings.backup_count
        self._backup.auto_cleanup = bool(getattr(self._settings, "backup_auto_cleanup", True))
        self._backup.backup_dir = getattr(self._settings, "backup_dir", "")
        if getattr(sys, "frozen", False) and self._backup.using_default_backup_dir:
            self._backup_path.setText(r"默认：用户文档\RIME 配置小工具\Backups")
            self._backup_path.setToolTip(str(self._backup.backup_dir))
        else:
            self._backup_path.setText(str(self._backup.backup_dir))
            self._backup_path.setToolTip("")
        self._btn_backup_default.setEnabled(bool(getattr(self._settings, "backup_dir", "")))
        self._refresh_backup_versions()
        self._cb_autodeploy.setChecked(self._settings.auto_deploy)
        self._cb_hotkey.setChecked(self._settings.hotkey_enabled)
        self._hotkey_seq.setKeySequence(QKeySequence(self._settings.hotkey_combo))

        # #6 部署器路径自动回填：探测到则写回设置并刷新输入框
        if not self._settings.deployer_path and self._deploy.deployer_path:
            self._settings.deployer_path = self._deploy.deployer_path
        self._deploy_path.setText(self._settings.deployer_path)

        # #7 备份份数下拉定位（1~5 直接选中；其余记为自定义）
        bc = self._settings.backup_count
        idx = self._backup_combo.findText(str(bc))
        if idx >= 0:
            self._backup_combo.setCurrentIndex(idx)
        else:
            self._backup_combo.setCurrentText("自定义…")

        self._cb_sandbox.setChecked(self._settings.sandbox_mode)
        self._pending_backup_settings = {
            "backup_count": self._settings.backup_count,
            "scheduled_backup_enabled": bool(getattr(self._settings, "scheduled_backup_enabled", False)),
            "backup_interval_days": max(1, min(365, int(getattr(self._settings, "backup_interval_days", 7)))),
            "backup_auto_cleanup": bool(getattr(self._settings, "backup_auto_cleanup", True)),
        }
        for control, value in (
            (self._cb_scheduled_backup, self._pending_backup_settings["scheduled_backup_enabled"]),
            (self._backup_interval, self._pending_backup_settings["backup_interval_days"]),
            (self._cb_backup_cleanup, self._pending_backup_settings["backup_auto_cleanup"]),
        ):
            control.blockSignals(True)
            try:
                control.setChecked(value) if isinstance(control, QCheckBox) else control.setValue(value)
            finally:
                control.blockSignals(False)
        self._refresh_next_backup_label()
        # Refreshing a persisted checkbox must not invoke its system action.
        self._cb_autostart.blockSignals(True)
        try:
            self._cb_autostart.setChecked(self._autostart.enabled)
        finally:
            self._cb_autostart.blockSignals(False)

        # 主题：按内部值 light/dark/ink 定位，仅刷新卡片选中态，不触发切换信号。
        self._set_theme_cards(self._settings.theme)

        self._refresh_schema_info()
        self._refresh_extension_files()
        self._refresh_managed_files()

        # 部署可用性；沙盒模式下禁止触发真实部署。
        if self._settings.sandbox_mode:
            self._lbl_deploy.setText("沙盒模式：不会触发真实部署。")
            self._btn_deploy.setEnabled(False)
        elif self._deploy.available:
            self._lbl_deploy.setText(f"部署器：{self._deploy.deployer_path}")
            self._btn_deploy.setEnabled(True)
        else:
            self._lbl_deploy.setText("未找到 WeaselDeployer.exe，可手动指定上方路径。")
            self._btn_deploy.setEnabled(False)

    def _refresh_schema_info(self) -> None:
        repo = self._schema_repo
        self._lbl_schema_name.setText(repo.schema_name() if repo else "")
        self._lbl_schema_ver.setText(repo.schema_version() if repo else "")
        self._lbl_schema_id.setText(repo.schema_id() if repo else "")

    def _refresh_managed_files(self) -> None:
        base = self._active_rime_dir()
        mode = "沙盒副本" if self._settings.sandbox_mode else "真实目录"
        for filename, _purpose in _MANAGED_FILES:
            file_path = base / filename if base else Path(filename)
            label = self._managed_labels[filename]
            label.setText(self._file_summary(file_path, mode))
            self._managed_open[filename].setEnabled(file_path.is_file())

    def _open_path(self, path: Path) -> None:
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_managed_file(self, filename: str) -> None:
        base = self._active_rime_dir()
        if base:
            self._open_path(base / filename)

    def _open_extension_file(self, key: str) -> None:
        path = self._extension_paths.get(key)
        if path:
            self._open_path(path)

    def _refresh_extension_files(self) -> None:
        base = self._active_rime_dir()
        specs = {
            # Static English dictionaries and the English input schema.  Do not
            # include cn_en user dictionaries here.
            "english": ("melt_eng.dict.yaml", "melt_eng.schema.yaml", "en_dicts/en*.dict.yaml"),
            # Per-layout Chinese-English user dictionaries actually referenced by
            # the shipped schemas.  Scripts and source files are not dictionaries.
            "mixed": ("en_dicts/cn_en*",),
            "display": ("pinyin_display.ini",),
        }
        for key, patterns in specs.items():
            files = []
            if base:
                for pattern in patterns:
                    files.extend(path for path in base.glob(pattern) if path.is_file())
            unique = sorted(set(files))
            if not unique:
                self._extension_paths.pop(key, None)
                self._extension_labels[key].setText("未找到")
                self._extension_open[key].setEnabled(False)
                continue
            self._extension_paths[key] = unique[0]
            self._extension_open[key].setEnabled(True)
            suffix = f" 等 {len(unique)} 个文件" if len(unique) > 1 else ""
            self._extension_labels[key].setText(f"已检测 · 只读{suffix}\n{unique[0]}")

    def _active_rime_dir(self) -> Path | None:
        if not self._settings.rime_dir:
            return None
        if self._settings.sandbox_mode:
            return user_config_dir() / "sandbox"
        return Path(self._settings.rime_dir)

    @staticmethod
    def _file_summary(path: Path, mode: str) -> str:
        if path.is_file():
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            size = SettingsWidget._format_size(stat.st_size)
            state = f"存在 · {size} · {modified}"
        else:
            state = "未找到"
        return f"{state}\n{mode}：{path}"

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    # ------------------------------------------------------------------ #
    def _on_hotkey_apply(self) -> None:
        """应用热键：由主窗口完成事务式重注册并回传结果。"""
        seq = self._hotkey_seq.keySequence().toString()
        if not seq:
            self.show_hotkey_apply_result(False, "请先按下新的热键组合。")
            return
        self.hotkeyComboChanged.emit(seq)  # type: ignore

    def show_hotkey_apply_result(
        self, ok: bool, text: str, applied_combo: str | None = None
    ) -> None:
        if applied_combo is not None:
            self._hotkey_seq.setKeySequence(QKeySequence(applied_combo))
        self._lbl_hotkey_status.setProperty("role", "success" if ok else "error")
        self._lbl_hotkey_status.style().unpolish(self._lbl_hotkey_status)
        self._lbl_hotkey_status.style().polish(self._lbl_hotkey_status)
        self._lbl_hotkey_status.setText(text)
        self._lbl_hotkey_status.show()

    def _on_hotkey_cancel(self) -> None:
        """取消：恢复显示为当前已生效的组合。"""
        self._hotkey_seq.setKeySequence(QKeySequence(self._settings.hotkey_combo))
        self._lbl_hotkey_status.hide()

    def _set_theme_cards(self, theme: str) -> None:
        """刷新三张主题卡片的选中态。"""
        if theme not in self._theme_cards:
            theme = "light"
        for key, card in self._theme_cards.items():
            card.set_selected(key == theme)

    def _on_theme_selected(self, theme: str) -> None:
        """主题切换：通过信号通知主窗口即时应用并持久化。"""
        if theme not in self._theme_cards:
            theme = "light"
        self._set_theme_cards(theme)
        self.themeChanged.emit(theme)  # type: ignore

    def _refresh_next_backup_label(self) -> None:
        pending = self._pending_backup_settings
        if not pending.get("scheduled_backup_enabled", False):
            self._lbl_next_backup.setText("定期备份未启用")
            return
        interval = max(1, int(pending.get("backup_interval_days", 7)))
        try:
            base = datetime.fromisoformat(str(getattr(self._settings, "last_backup_at", "")))
        except ValueError:
            base = datetime.now()
        self._lbl_next_backup.setText("预计下次备份日期：" + (base + timedelta(days=interval)).strftime("%Y-%m-%d"))

    def _open_backup_dir(self) -> None:
        directory = self._backup.backup_dir
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _open_project_document(self, relative: str) -> None:
        QDesktopServices.openUrl(QUrl(REPOSITORY_URL.rstrip("/") + "/blob/main/" + relative))

    def _on_backup_changed(self, text: str) -> None:
        if text == "自定义…":
            from PySide6.QtWidgets import QInputDialog

            val, ok = QInputDialog.getInt(
                self, "备份保留份数", "输入 1~365：",
                int(self._pending_backup_settings.get("backup_count", self._settings.backup_count)), 1, 365)
            if ok:
                self._pending_backup_settings["backup_count"] = val
            self._backup_combo.blockSignals(True)
            self._backup_combo.setCurrentText(str(self._pending_backup_settings.get("backup_count", self._settings.backup_count)))
            self._backup_combo.blockSignals(False)
            return
        try:
            self._pending_backup_settings["backup_count"] = int(text)
        except ValueError:
            return

    def _on_scheduled_backup_toggled(self, enabled: bool) -> None:
        self._pending_backup_settings["scheduled_backup_enabled"] = enabled
        self._refresh_next_backup_label()

    def _on_backup_interval_changed(self, value: int) -> None:
        self._pending_backup_settings["backup_interval_days"] = value
        self._refresh_next_backup_label()

    def _on_backup_cleanup_toggled(self, enabled: bool) -> None:
        self._pending_backup_settings["backup_auto_cleanup"] = enabled

    def _save_backup_settings(self) -> None:
        pending = self._pending_backup_settings
        for key in ("backup_count", "scheduled_backup_enabled", "backup_interval_days", "backup_auto_cleanup"):
            self._settings.set(key, pending[key])
        self._backup.keep = int(pending["backup_count"])
        self._backup.auto_cleanup = bool(pending["backup_auto_cleanup"])
        self._lbl_backup_status.setProperty("role", "success")
        self._lbl_backup_status.setVisible(True)
        self._lbl_backup_status.setText(
            "备份设置已保存："
            + (f"每 {pending['backup_interval_days']} 天定期备份。" if pending["scheduled_backup_enabled"] else "已关闭定期备份。")
        )
        self._refresh_next_backup_label()

    def perform_managed_backup(self, scheduled: bool = False) -> tuple[bool, str]:
        """Back up every file managed by this app, including display metadata."""
        self._backup.keep = self._settings.backup_count
        self._backup.auto_cleanup = bool(getattr(self._settings, "backup_auto_cleanup", True))
        files = [PHRASE_FILENAME, SCHEMA_FILENAME, SYMBOLS_FILENAME, "pinyin_display.ini"]
        report = self._backup.backup_files_report(files)
        saved = sum(path is not None for path in report.saved.values())
        removed = len(report.removed)
        self._settings.set("last_backup_at", datetime.now().isoformat(timespec="seconds"))
        message = f"已备份 {saved} 个现有受管文件。"
        if removed:
            message += f"已清理 {removed} 份旧备份。"
        elif self._backup.auto_cleanup:
            message += "无需清理旧备份。"
        else:
            message += "未启用旧备份清理。"
        self._lbl_backup_status.setProperty("role", "success" if saved else "warning")
        self._lbl_backup_status.setVisible(True)
        self._lbl_backup_status.setText(message if not scheduled else f"定期备份：{message}")
        self._refresh_backup_versions(force=True)
        self._refresh_next_backup_label()
        return True, message

    def _on_backup_now(self) -> None:
        self.perform_managed_backup()

    def _on_check_update(self) -> None:
        self._btn_check_update.setEnabled(False)
        self._lbl_update_status.setProperty("role", "info")
        self._lbl_update_status.setVisible(True)
        self._lbl_update_status.setText("正在检查更新…")
        self._update_poll.start()
        threading.Thread(
            target=self._check_update_worker,
            name="rime-config-update",
            daemon=True,
        ).start()

    def _check_update_worker(self) -> None:
        try:
            service = UpdateService()
            release, message = service.check(__version__)
            if release is None:
                result = (False, message, False)
            else:
                ok, message = service.download_replace_and_restart(release)
                result = (ok, message, ok)
        except Exception as exc:
            result = (False, f"检查更新失败：{exc}", False)
        self._update_results.put(result)

    def _drain_update_results(self) -> None:
        try:
            result = self._update_results.get_nowait()
        except queue.Empty:
            return
        self._update_poll.stop()
        self._on_update_finished(*result)

    def _on_update_finished(self, ok: bool, message: str, restart: bool) -> None:
        self._lbl_update_status.setProperty("role", "success" if ok else "warning")
        self._lbl_update_status.setVisible(True)
        self._lbl_update_status.setText(message)
        self._btn_check_update.setEnabled(True)
        if restart:
            from PySide6.QtWidgets import QApplication
            QApplication.quit()

    def _on_deploy_path_changed(self) -> None:
        self._settings.deployer_path = self._deploy_path.text().strip()
        # 重新探测部署器可用性（手动路径优先）
        self._deploy.redetect()
        self.refresh()

    def _on_deploy_browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        p, _ = QFileDialog.getOpenFileName(
            self, "选择 WeaselDeployer.exe", "", "可执行文件 (*.exe)")
        if p:
            self._deploy_path.setText(p)
            self._on_deploy_path_changed()

    # ------------------------------------------------------------------ #
    def _refresh_backup_versions(self, _index: int = -1, force: bool = False) -> None:
        filename = self._restore_file.currentData()
        try:
            modified = self._backup.backup_dir.stat().st_mtime_ns
        except OSError:
            modified = 0
        signature = (str(filename or ""), str(self._backup.backup_dir), modified)
        if not force and signature == self._backup_versions_signature:
            return
        self._backup_versions_signature = signature
        self._restore_version.clear()
        if not filename:
            self._btn_restore.setEnabled(False)
            return
        for path in self._backup.list_backups(str(filename)):
            self._restore_version.addItem(path.name, str(path))
        self._btn_restore.setEnabled(self._restore_version.count() > 0)

    def _on_backup_browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        directory = QFileDialog.getExistingDirectory(
            self, "选择备份目录", str(self._backup.backup_dir)
        )
        if directory:
            self._settings.set("backup_dir", directory)
            self._backup.backup_dir = directory
            self.refresh()

    def _on_backup_default(self) -> None:
        self._settings.set("backup_dir", "")
        self._backup.backup_dir = ""
        self.refresh()

    def _on_restore_backup(self) -> None:
        filename = str(self._restore_file.currentData() or "")
        backup_path = str(self._restore_version.currentData() or "")
        if not filename or not backup_path:
            return
        answer = QMessageBox.question(
            self, "恢复备份",
            f"将用所选备份覆盖 {filename}。恢复前会先备份当前文件，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        companion_backup = None
        if filename == PHRASE_FILENAME:
            try:
                selected_mtime = Path(backup_path).stat().st_mtime
                candidates = self._backup.list_backups("pinyin_display.ini")
                if candidates:
                    nearest = min(
                        candidates,
                        key=lambda path: abs(path.stat().st_mtime - selected_mtime),
                    )
                    if abs(nearest.stat().st_mtime - selected_mtime) <= 5:
                        companion_backup = nearest
            except OSError:
                companion_backup = None
        self._backup.backup(filename)
        if filename == PHRASE_FILENAME:
            self._backup.backup("pinyin_display.ini")
        ok = self._backup.restore(backup_path, filename)
        if ok and companion_backup is not None:
            self._backup.restore(str(companion_backup), "pinyin_display.ini")
        if ok:
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText(f"已恢复：{filename}")
            self.rimeDirChanged.emit(self._settings.rime_dir)
            self._refresh_backup_versions(force=True)
        else:
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText("恢复失败：备份文件不存在或无法读取。")

    def _on_browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(self, "选择 Rime 目录",
                                             self._settings.rime_dir or "")
        if d:
            self._settings.rime_dir = d
            self._rime_dir.setText(d)
            self._on_rime_dir_changed()

    def _on_detect(self) -> None:
        from src.config.rime_path_detector import RimePathDetector

        # 同时探测 Rime 目录 + 部署器路径（同属部署功能）
        detected = RimePathDetector().detect()
        if detected:
            self._settings.rime_dir = detected
            self._rime_dir.setText(detected)
        self._deploy.redetect()
        if self._deploy.deployer_path:
            self._settings.deployer_path = self._deploy.deployer_path
        self._on_rime_dir_changed()
        self.refresh()

    def _on_rime_dir_changed(self) -> None:
        """Rime 目录变化：通过信号通知主窗口重建仓储（由主窗口连接）。"""
        if hasattr(self, "rimeDirChanged"):
            self.rimeDirChanged.emit(self._settings.rime_dir)  # type: ignore

    def _on_hotkey_toggled(self, checked: bool) -> None:
        self._settings.hotkey_enabled = checked
        if hasattr(self, "hotkeyToggled"):
            self.hotkeyToggled.emit(checked)  # type: ignore

    def _on_sandbox_toggled(self, checked: bool) -> None:
        self._settings.sandbox_mode = checked
        self.refresh()
        if hasattr(self, "sandboxToggled"):
            self.sandboxToggled.emit(checked)  # type: ignore

    def _on_autostart_toggled(self, checked: bool) -> None:
        if checked:
            ok = self._autostart.enable()
            if not ok:
                # 启用失败：不得静默显示为成功，回退勾选并提示
                self._cb_autostart.setChecked(False)
                self._settings.autostart = False
                QMessageBox.warning(
                    self, "开机自启",
                    "启用开机自启失败（可能缺少权限或 pywin32）。\n"
                    "可在系统「启动」文件夹手动创建本程序快捷方式。")
                return
            self._settings.autostart = True
        else:
            self._autostart.disable()
            self._settings.autostart = False

    def _on_deploy(self) -> None:
        ok, msg = self._deploy.deploy()
        from PySide6.QtWidgets import QMessageBox

        if ok:
            QMessageBox.information(self, "部署", msg)
        else:
            QMessageBox.warning(self, "部署", msg)
