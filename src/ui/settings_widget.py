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
from types import SimpleNamespace

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
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
    QTabWidget,
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
from src.ui.wanxiang_update_notice import (
    WanxiangCompatibilityDialog, WanxiangStatusPanel,
    WanxiangUpdateConfirmationDialog,
)
from src.ui.wanxiang_deployment_dialog import WanxiangDeploymentDialog
from src.ui.application_update_dialog import ApplicationUpdateDialog
from src.ui.restore_preview_dialog import RestorePreviewDialog
from src.ui.click_activated_spin import ClickActivatedSpinBox
from src.settings import Settings
from src.service.autostart import Autostart
from src.service.deploy_service import DeployService
from src.service.data_engine_service import DataEngineClient
from src.service.managed_files import managed_scheme
from src.service.dictionary_catalog import (
    dictionary_catalog_signature,
    dictionary_catalog_snapshot,
)
from src.service.rime_profiles import scheme_title
from src.service.input_diagnostics_launcher import (
    diagnostics_process_running,
    launch_input_diagnostics,
)
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
    wanxiangFilesChanged = Signal()

    def __init__(self, settings: Settings, autostart: Autostart,
                 deploy: DeployService, schema_repo: SchemaRepo | None = None,
                 backup: BackupService | None = None,
                 health_opener=None,
                 dictionary_center_opener=None,
                 diagnostics_launcher=None,
                 diagnostics_process_checker=None,
                 data_engine_client: DataEngineClient | None = None,
                 environment_provider=None,
                 deploy_request=None,
                 wanxiang_deploy_request=None,
                 wanxiang_update_service_provider=None,
                 background_tasks_enabled: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._autostart = autostart
        self._deploy = deploy
        self._schema_repo = schema_repo
        self._health_opener = health_opener
        self._dictionary_center_opener = dictionary_center_opener
        self._data_engine_client = data_engine_client
        self._environment_provider = environment_provider
        self._deploy_request = deploy_request
        self._wanxiang_deploy_request = wanxiang_deploy_request or deploy_request
        self._wanxiang_update_service_provider = wanxiang_update_service_provider
        self._background_tasks_enabled = bool(background_tasks_enabled)
        self._disposed = False
        self._wanxiang_results: queue.SimpleQueue = queue.SimpleQueue()
        self._wanxiang_report = None
        self._wanxiang_deploy_pending = False
        self._wanxiang_deploy_mode = ""
        self._wanxiang_force_available = False
        self._wanxiang_poll = QTimer(self)
        self._wanxiang_poll.setInterval(100)
        self._wanxiang_poll.timeout.connect(self._drain_wanxiang_result)
        self._diagnostics_launcher = diagnostics_launcher or launch_input_diagnostics
        self._diagnostics_process_checker = (
            diagnostics_process_checker or diagnostics_process_running
        )
        self._diagnostics_pid: int | None = None
        self._diagnostics_poll = QTimer(self)
        self._diagnostics_poll.setInterval(500)
        self._diagnostics_poll.timeout.connect(self._poll_input_diagnostics)
        self._backup = backup or BackupService(
            settings.rime_dir, getattr(settings, "backup_count", 5),
            getattr(settings, "backup_dir", ""))
        self._managed_labels: dict[str, QLabel] = {}
        self._managed_open: dict[str, QPushButton] = {}
        self._extension_labels: dict[str, QLabel] = {}
        self._extension_open: dict[str, QPushButton] = {}
        self._extension_paths: dict[str, Path] = {}
        self._dictionary_results: queue.SimpleQueue[tuple[int, dict | None, str]] = queue.SimpleQueue()
        self._dictionary_generation = 0
        self._dictionary_refresh_key: tuple | None = None
        self._dictionary_poll = QTimer(self)
        self._dictionary_poll.setInterval(50)
        self._dictionary_poll.timeout.connect(self._drain_dictionary_results)
        self._backup_versions_signature: tuple[str, str, int] | None = None
        self._update_results: queue.SimpleQueue[tuple] = queue.SimpleQueue()
        self._update_poll = QTimer(self)
        self._update_poll.setInterval(100)
        self._update_poll.timeout.connect(self._drain_update_results)
        self._application_update_progress = ApplicationUpdateDialog(self)
        self._wanxiang_test_progress: WanxiangDeploymentDialog | None = None
        self._wanxiang_test_timer = QTimer(self)
        self._wanxiang_test_timer.setSingleShot(True)
        self._wanxiang_test_timer.timeout.connect(
            self._finish_wanxiang_deployment_test
        )
        self._build_ui()
        self.refresh()
        if self._background_tasks_enabled:
            QTimer.singleShot(1200, self._maybe_check_wanxiang)

    def dispose(self) -> None:
        """Invalidate background results and stop every UI-owned poll timer."""
        if self._disposed:
            return
        self._disposed = True
        self._dictionary_generation += 1
        for timer in (
            self._wanxiang_poll, self._diagnostics_poll,
            self._dictionary_poll, self._update_poll, self._wanxiang_test_timer,
        ):
            timer.stop()
        if self._wanxiang_test_progress is not None:
            self._wanxiang_test_progress.finish()
        self._application_update_progress.finish()
        if self._data_engine_client is not None:
            self._data_engine_client.cancel_pending()

    def _scheme_resolution(self):
        return self._environment_provider() if self._environment_provider is not None else None

    def _managed_scheme_files(self):
        resolution = self._scheme_resolution()
        return getattr(resolution, "files", None) or managed_scheme("rime_frost")

    def _current_schema_id(self) -> str:
        resolution = self._scheme_resolution()
        return str(getattr(resolution, "schema_id", "") or "rime_frost")

    def _scheme_backup_policy(self) -> dict[str, object]:
        if hasattr(self._settings, "scheme_backup_policy"):
            return self._settings.scheme_backup_policy(self._current_schema_id())
        return {
            "backup_count": int(getattr(self._settings, "backup_count", 5)),
            "last_backup_at": str(getattr(self._settings, "last_backup_at", "")),
        }

    def _set_scheme_backup_policy(self, **values) -> None:
        if hasattr(self._settings, "set_scheme_backup_policy"):
            self._settings.set_scheme_backup_policy(self._current_schema_id(), **values)
            return
        if "backup_count" in values:
            self._settings.set("backup_count", int(values["backup_count"]))
        if "last_backup_at" in values:
            self._settings.set("last_backup_at", str(values["last_backup_at"]))

    def _settings_get(self, key: str, default=None):
        getter = getattr(self._settings, "get", None)
        return getter(key, default) if callable(getter) else getattr(self._settings, key, default)

    def _on_library_health(self) -> None:
        if self._health_opener is not None:
            self._health_opener()

    def _on_input_diagnostics(self) -> None:
        result = self._diagnostics_launcher()
        ok, message = result[:2]
        pid = result[2] if len(result) > 2 else None
        self._diagnostics_pid = int(pid) if ok and pid else None
        if self._diagnostics_pid is not None:
            self._diagnostics_poll.start()
        else:
            self._diagnostics_poll.stop()
        self._lbl_diagnostics_status.setProperty("role", "success" if ok else "error")
        self._lbl_diagnostics_status.setText(message)
        self._lbl_diagnostics_status.setVisible(True)

    def _poll_input_diagnostics(self) -> None:
        if self._diagnostics_pid is None:
            self._diagnostics_poll.stop()
            return
        if self._diagnostics_process_checker(self._diagnostics_pid):
            return
        self._diagnostics_pid = None
        self._diagnostics_poll.stop()
        self._lbl_diagnostics_status.setProperty("role", "muted")
        self._lbl_diagnostics_status.setText("输入诊断已停止。")
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
    def _build_rime_group(self) -> QGroupBox:
        g_rime = QGroupBox("Rime 文件位置与重新部署")
        rime_form = QFormLayout(g_rime)

        rime_row = QHBoxLayout()
        self._rime_dir = QLineEdit()
        self._rime_dir.setReadOnly(True)
        self._btn_browse = QPushButton("浏览…")
        self._btn_browse.clicked.connect(self._on_browse)
        self._btn_detect = QPushButton("重新探测")
        self._btn_detect.clicked.connect(self._on_detect)
        self._btn_rime_edit = QPushButton("手动编辑")
        self._btn_rime_edit.clicked.connect(lambda: self._rime_dir.setReadOnly(False))
        self._btn_rime_apply = QPushButton("应用")
        self._btn_rime_apply.clicked.connect(self._apply_rime_dir)
        rime_row.addWidget(self._rime_dir, 1)
        rime_row.addWidget(self._btn_browse)
        rime_row.addWidget(self._btn_detect)
        rime_row.addWidget(self._btn_rime_edit)
        rime_row.addWidget(self._btn_rime_apply)
        rime_form.addRow("目录：", rime_row)

        # 部署器路径（自动探测后自动回填）
        dp_row = QHBoxLayout()
        self._deploy_path = QLineEdit()
        self._deploy_path.setReadOnly(True)
        self._deploy_path.setPlaceholderText("未指定时自动查找小狼毫的重新部署程序")
        self._btn_deploy_browse = QPushButton("浏览…")
        self._btn_deploy_browse.clicked.connect(self._on_deploy_browse)
        self._btn_deploy_edit = QPushButton("手动编辑")
        self._btn_deploy_edit.clicked.connect(lambda: self._deploy_path.setReadOnly(False))
        self._btn_deploy_apply = QPushButton("应用")
        self._btn_deploy_apply.clicked.connect(self._on_deploy_path_changed)
        dp_row.addWidget(self._deploy_path, 1)
        dp_row.addWidget(self._btn_deploy_browse)
        dp_row.addWidget(self._btn_deploy_edit)
        dp_row.addWidget(self._btn_deploy_apply)
        rime_form.addRow("重新部署程序：", dp_row)

        self._lbl_deploy = QLabel()
        rime_form.addRow(self._lbl_deploy)

        self._btn_deploy = QPushButton("立即重新部署")
        self._btn_deploy.setObjectName("Primary")
        self._btn_deploy.clicked.connect(self._on_deploy)
        rime_form.addRow(self._btn_deploy)
        return g_rime

    def _build_dictionary_groups(
            self) -> tuple[QGroupBox, QGroupBox, QGroupBox, QGroupBox]:
        g_schema = QGroupBox("当前输入方案")
        schema_form = QFormLayout(g_schema)
        self._lbl_schema_name = QLabel()
        self._lbl_schema_ver = QLabel()
        self._lbl_schema_id = QLabel()
        schema_form.addRow("名称：", self._lbl_schema_name)
        schema_form.addRow("版本：", self._lbl_schema_ver)
        schema_form.addRow("内部标识：", self._lbl_schema_id)

        # 受管文件
        g_files = QGroupBox("本工具管理的文件")
        files_form = QFormLayout(g_files)
        for key, purpose in (("phrase", "个人词库"), ("schema", "输入方案"), ("symbols", "符号表")):
            label = QLabel()
            label.setWordWrap(True)
            label.setProperty("role", "info")
            self._managed_labels[key] = label
            open_button = QPushButton("打开")
            open_button.setFixedWidth(58)
            open_button.setEnabled(False)
            open_button.clicked.connect(lambda _=False, name=key: self._open_managed_file(name))
            self._managed_open[key] = open_button
            compatibility_name = {
                "phrase": PHRASE_FILENAME,
                "schema": SCHEMA_FILENAME,
                "symbols": SYMBOLS_FILENAME,
            }[key]
            self._managed_labels[compatibility_name] = label
            self._managed_open[compatibility_name] = open_button
            row = QHBoxLayout()
            row.addWidget(label, 1)
            row.addWidget(open_button)
            files_form.addRow(f"{purpose}：", row)

        self._dictionary_catalog_label = QLabel()
        self._dictionary_catalog_label.setWordWrap(True)
        self._dictionary_catalog_label.setProperty("role", "info")
        self._dictionary_catalog_button = QPushButton("管理")
        self._dictionary_catalog_button.setFixedWidth(58)
        self._dictionary_catalog_button.clicked.connect(
            lambda: self._open_dictionary_center("all")
        )
        catalog_row = QHBoxLayout()
        catalog_row.addWidget(self._dictionary_catalog_label, 1)
        catalog_row.addWidget(self._dictionary_catalog_button)
        files_form.addRow("当前方案词典：", catalog_row)

        g_extensions = QGroupBox("其他词库（仅查看）")
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

        self._extension_catalog_label = QLabel()
        self._extension_catalog_label.setWordWrap(True)
        self._extension_catalog_label.setProperty("role", "info")
        self._extension_catalog_button = QPushButton("查看")
        self._extension_catalog_button.setFixedWidth(58)
        self._extension_catalog_button.clicked.connect(
            lambda: self._open_dictionary_center("extensions")
        )
        extension_catalog_row = QHBoxLayout()
        extension_catalog_row.addWidget(self._extension_catalog_label, 1)
        extension_catalog_row.addWidget(self._extension_catalog_button)
        extensions_form.addRow("当前方案扩展词典：", extension_catalog_row)

        g_library = QGroupBox("词库维护")
        library_layout = QHBoxLayout(g_library)
        self._btn_library_health = QPushButton("词库检查")
        self._btn_library_health.setObjectName("Primary")
        self._btn_library_health.setEnabled(self._health_opener is not None)
        self._btn_library_health.clicked.connect(self._on_library_health)
        library_layout.addWidget(QLabel("检查空值、权重、重复项和无效分组。"))
        library_layout.addStretch(1)
        library_layout.addWidget(self._btn_library_health)
        return g_files, g_extensions, g_schema, g_library

    def _build_behavior_group(self) -> QGroupBox:
        g_behavior = QGroupBox("行为")
        b_form = QFormLayout(g_behavior)
        self._cb_autodeploy = VisibleCheckBox("写文件后自动重新部署")
        self._cb_hotkey = VisibleCheckBox("启用全局热键收藏")
        # #8 热键组合：键盘捕获（按下组合键即暂存，点『应用热键』才重注册）
        self._hotkey_seq = QKeySequenceEdit()
        self._hotkey_seq.setMaximumWidth(260)
        self._btn_hotkey_apply = QPushButton("应用热键")
        self._btn_hotkey_apply.setObjectName("Primary")
        self._btn_hotkey_apply.clicked.connect(self._on_hotkey_apply)
        self._btn_hotkey_cancel = QPushButton("取消")
        self._btn_hotkey_cancel.clicked.connect(self._on_hotkey_cancel)
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

        switches = QWidget(g_behavior)
        switches_row = QHBoxLayout(switches)
        switches_row.setContentsMargins(0, 0, 0, 0)
        switches_row.addWidget(self._cb_autodeploy)
        switches_row.addSpacing(24)
        switches_row.addWidget(self._cb_hotkey)
        switches_row.addStretch(1)
        b_form.addRow(switches)
        hotkey_editor = QWidget(g_behavior)
        hotkey_editor_row = QHBoxLayout(hotkey_editor)
        hotkey_editor_row.setContentsMargins(0, 0, 0, 0)
        hotkey_editor_row.addWidget(self._hotkey_seq)
        hotkey_editor_row.addWidget(self._btn_hotkey_apply)
        hotkey_editor_row.addWidget(self._btn_hotkey_cancel)
        hotkey_editor_row.addStretch(1)
        b_form.addRow("热键组合（按下组合键）：", hotkey_editor)
        b_form.addRow("", self._lbl_hotkey_status)
        return g_behavior

    def _build_theme_group(self) -> QGroupBox:
        group = QGroupBox("主题")
        layout = QVBoxLayout(group)
        layout.addWidget(self._theme_selector)
        return group

    def _build_diagnostics_groups(self) -> tuple[QGroupBox, QGroupBox]:
        g_diagnostics = QGroupBox("输入诊断（按需运行）")
        diagnostics_layout = QVBoxLayout(g_diagnostics)
        diagnostics_row = QHBoxLayout()
        diagnostics_note = QLabel(
            "独立诊断程序默认不运行。遇到候选窗、按键或输入法异常时再启动；"
            "关闭诊断窗口后监控随即停止。"
        )
        diagnostics_note.setWordWrap(True)
        self._btn_input_diagnostics = QPushButton("启动输入诊断")
        self._btn_input_diagnostics.setObjectName("Primary")
        self._btn_input_diagnostics.clicked.connect(self._on_input_diagnostics)
        diagnostics_row.addWidget(diagnostics_note, 1)
        diagnostics_row.addWidget(self._btn_input_diagnostics)
        self._lbl_diagnostics_status = QLabel()
        self._lbl_diagnostics_status.setWordWrap(True)
        self._lbl_diagnostics_status.setVisible(False)
        diagnostics_layout.addLayout(diagnostics_row)
        diagnostics_layout.addWidget(self._lbl_diagnostics_status)

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
        return g_diagnostics, g_github

    def _build_wanxiang_update_group(self) -> QGroupBox:
        group = QGroupBox("万象更新", self)
        layout = QVBoxLayout(group)
        self._cb_wanxiang_check = VisibleCheckBox("每天自动检查万象拼音更新", group)
        self._cb_wanxiang_update = VisibleCheckBox("确认更新安全后自动安装", group)
        self._cb_wanxiang_check.toggled.connect(
            lambda value: self._settings.set("wanxiang_auto_check", bool(value)))
        self._cb_wanxiang_update.toggled.connect(
            lambda value: self._settings.set("wanxiang_auto_update", bool(value)))
        row = QHBoxLayout()
        self._btn_wanxiang_check = QPushButton("立即检查", group)
        self._btn_wanxiang_check.clicked.connect(self._start_wanxiang_check)
        self._btn_wanxiang_update = QPushButton("安装更新", group)
        self._btn_wanxiang_update.setObjectName("Primary")
        self._btn_wanxiang_update.setEnabled(False)
        self._btn_wanxiang_update.clicked.connect(
            lambda: self._start_wanxiang_update(force=False))
        self._btn_wanxiang_force = QPushButton("强制更新", group)
        self._btn_wanxiang_force.setObjectName("Danger")
        self._btn_wanxiang_force.setToolTip(
            "仅在官方文件被本地修改、普通更新无法继续时使用；个人词库和自定义配置仍受保护。"
        )
        self._btn_wanxiang_force.setVisible(False)
        self._btn_wanxiang_force.clicked.connect(
            lambda: self._start_wanxiang_update(force=True))
        self._btn_wanxiang_restore = QPushButton("恢复更新前版本", group)
        self._btn_wanxiang_restore.clicked.connect(self._start_wanxiang_restore)
        self._btn_wanxiang_info = QPushButton("查看适配说明", group)
        self._btn_wanxiang_info.setEnabled(False)
        self._btn_wanxiang_info.clicked.connect(self._show_wanxiang_explanation)
        self._btn_wanxiang_test = QPushButton("模拟更新确认（仅测试）", group)
        self._btn_wanxiang_test.setVisible(
            os.environ.get("RIME_CONFIG_GUI_TEST") == "1"
        )
        self._btn_wanxiang_test.setToolTip(
            "只显示更新前确认框；不会下载、写文件或调用小狼毫。"
        )
        self._btn_wanxiang_test.clicked.connect(
            self._show_wanxiang_update_test_confirmation
        )
        self._btn_wanxiang_deployment_test = QPushButton(
            "模拟更新部署（冻结 10 秒）", group
        )
        self._btn_wanxiang_deployment_test.setToolTip(
            "显示应用级冻结提示并在 10 秒后自动恢复；不会执行真实部署。"
        )
        self._btn_wanxiang_deployment_test.clicked.connect(
            self._show_wanxiang_deployment_test
        )
        self._btn_quick_capture_test = QPushButton(
            "模拟采集窗口（不写入）", group
        )
        self._btn_quick_capture_test.setToolTip(
            "预填示例词并演示多编码与删除默认编码；保存结果不会写入词库。"
        )
        self._btn_quick_capture_test.clicked.connect(
            self._show_quick_capture_test
        )
        self._wanxiang_test_tools = QWidget(group)
        self._wanxiang_test_tools.setVisible(
            os.environ.get("RIME_CONFIG_GUI_TEST") == "1"
        )
        test_row = QHBoxLayout(self._wanxiang_test_tools)
        test_row.setContentsMargins(0, 0, 0, 0)
        test_label = QLabel("GUI 测试：", self._wanxiang_test_tools)
        test_label.setProperty("role", "muted")
        test_row.addWidget(test_label)
        test_row.addWidget(self._btn_wanxiang_test)
        test_row.addWidget(self._btn_wanxiang_deployment_test)
        test_row.addWidget(self._btn_quick_capture_test)
        test_row.addStretch(1)
        row.addWidget(self._btn_wanxiang_check)
        row.addWidget(self._btn_wanxiang_update)
        row.addWidget(self._btn_wanxiang_force)
        row.addWidget(self._btn_wanxiang_restore)
        row.addWidget(self._btn_wanxiang_info)
        row.addStretch(1)
        self._lbl_wanxiang_recovery = QLabel("暂无独立更新备份", group)
        self._lbl_wanxiang_recovery.setProperty("role", "muted")
        self._lbl_wanxiang_recovery.setWordWrap(True)
        self._wanxiang_status = WanxiangStatusPanel(group)
        self._lbl_wanxiang_update = self._wanxiang_status.result
        layout.addWidget(self._cb_wanxiang_check)
        layout.addWidget(self._cb_wanxiang_update)
        layout.addLayout(row)
        layout.addWidget(self._wanxiang_test_tools)
        layout.addWidget(self._wanxiang_status)
        layout.addWidget(self._lbl_wanxiang_recovery)
        return group

    def _show_wanxiang_explanation(self) -> None:
        if self._wanxiang_report is None:
            QMessageBox.information(self, "万象适配说明", "请先点击“立即检查”获取当前状态。")
            return
        WanxiangCompatibilityDialog(self._wanxiang_report, self).exec()

    def _show_wanxiang_update_test_confirmation(self) -> None:
        """Open a no-side-effect confirmation preview in GUI test launches only."""
        if os.environ.get("RIME_CONFIG_GUI_TEST") != "1":
            return
        current = "当前已安装版本"
        if self._wanxiang_update_service_provider is not None:
            try:
                current = (
                    self._wanxiang_update_service_provider().current_version()
                    or current
                )
            except Exception:
                pass
        report = SimpleNamespace(
            current_version=current,
            new_version="模拟新版本",
            requires_confirmation=False,
        )
        result = WanxiangUpdateConfirmationDialog(report, parent=self).exec()
        if result == QDialog.DialogCode.Accepted:
            self._lbl_wanxiang_update.setText(
                "模拟确认已完成：未下载、未写入文件、未调用小狼毫。"
            )

    def _show_wanxiang_deployment_test(self) -> None:
        """Exercise the real application-modal progress without side effects."""
        if os.environ.get("RIME_CONFIG_GUI_TEST") != "1":
            return
        if (self._wanxiang_test_progress is not None
                and self._wanxiang_test_progress.active):
            return
        self._wanxiang_test_progress = WanxiangDeploymentDialog(self.window())
        self._wanxiang_test_progress.start(
            "[模拟测试] 正在重新部署并重建万象词典…"
        )
        self._lbl_wanxiang_update.setText(
            "正在模拟更新部署：应用将在 10 秒后自动恢复；未执行任何真实操作。"
        )
        self._wanxiang_test_timer.start(10_000)

    def _finish_wanxiang_deployment_test(self) -> None:
        progress = self._wanxiang_test_progress
        if progress is None:
            return
        progress.finish()
        progress.deleteLater()
        self._wanxiang_test_progress = None
        if not self._disposed:
            self._lbl_wanxiang_update.setText(
                "模拟更新部署已结束：应用已恢复，未写入文件、未调用小狼毫。"
            )

    def _show_quick_capture_test(self) -> None:
        """Open the real capture dialog and discard the accepted result."""
        if os.environ.get("RIME_CONFIG_GUI_TEST") != "1":
            return
        from src.ui.quick_add_dialog import QuickAddDialog

        dialog = QuickAddDialog(
            prefill_text="银行",
            code_alphabet=(
                self._schema_repo.speller_alphabet()
                if self._schema_repo is not None else ""
            ),
            parent=self.window(),
        )
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.get_values()
            main_code = values.get("display_code") or "（默认编码已删除）"
            extras = list(values.get("additional_codes") or [])
            extra_text = "、".join(extras) if extras else "（无）"
            self._lbl_wanxiang_update.setText(
                f"模拟采集结果：默认编码 {main_code}；保留编码 {extra_text}。"
                "未写入个人词库，未触发部署。"
            )
        finally:
            dialog.dispose()
            dialog.deleteLater()

    def _build_backup_group(self) -> QGroupBox:
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
        self._refresh_restore_file_choices()
        self._restore_version = ClickActivatedComboBox()
        self._btn_restore = QPushButton("恢复所选备份")
        self._btn_restore.setObjectName("Primary")
        self._btn_restore.clicked.connect(self._on_restore_backup)
        self._btn_restore_preview = QPushButton("恢复前比较")
        self._btn_restore_preview.clicked.connect(self._preview_restore_backup)
        self._restore_file.currentIndexChanged.connect(self._refresh_backup_versions)

        backup_settings = QWidget(g_backup)
        settings_grid = QGridLayout(backup_settings)
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(9)
        settings_grid.setVerticalSpacing(7)
        action_width = 112
        for button in (
            self._btn_backup_settings_save, self._btn_backup_now,
            self._btn_restore, self._btn_restore_preview,
        ):
            button.setFixedWidth(action_width)

        settings_grid.addWidget(QLabel("保留份数："), 0, 0)
        settings_grid.addWidget(self._backup_combo, 0, 1)
        settings_grid.addWidget(self._cb_scheduled_backup, 0, 2)
        settings_grid.addWidget(self._cb_backup_cleanup, 0, 3)
        settings_grid.addWidget(self._btn_backup_settings_save, 0, 5)

        settings_grid.addWidget(QLabel("恢复："), 1, 0)
        settings_grid.addWidget(self._restore_file, 1, 1)
        settings_grid.addWidget(self._restore_version, 1, 2, 1, 2)
        settings_grid.addWidget(self._btn_restore_preview, 1, 4)
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
        return g_backup

    def _build_autostart_group(self) -> QGroupBox:
        g_start = QGroupBox("开机自启")
        s_form = QFormLayout(g_start)
        self._cb_autostart = VisibleCheckBox("开机自动启动")
        self._cb_autostart.toggled.connect(self._on_autostart_toggled)
        autostart_row = QWidget()
        autostart_layout = QHBoxLayout(autostart_row)
        autostart_layout.setContentsMargins(0, 0, 0, 0)
        autostart_layout.setSpacing(10)
        autostart_layout.addWidget(self._cb_autostart)
        self._lbl_autostart_status = QLabel()
        self._lbl_autostart_status.setMinimumWidth(190)
        self._lbl_autostart_status.setProperty("role", "neutral")
        autostart_layout.addWidget(self._lbl_autostart_status)
        autostart_layout.addStretch(1)
        s_form.addRow(autostart_row)
        return g_start

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 14)
        outer.setSpacing(12)

        page_header = QWidget(self)
        page_header.setObjectName("PageHeader")
        header_layout = QVBoxLayout(page_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(3)
        title = QLabel("设置", page_header)
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "按任务分类管理 Rime 环境、词典文件、备份与诊断",
            page_header,
        )
        subtitle.setObjectName("PageSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(page_header)

        g_rime = self._build_rime_group()
        g_files, g_extensions, g_schema, g_library = (
            self._build_dictionary_groups()
        )
        g_behavior = self._build_behavior_group()
        g_theme = self._build_theme_group()
        if self._dev_controls_visible:
            g_rime.layout().addRow(self._cb_sandbox)
        g_diagnostics, g_github = self._build_diagnostics_groups()
        g_wanxiang_update = self._build_wanxiang_update_group()
        g_backup = self._build_backup_group()
        g_start = self._build_autostart_group()
        self._sections = QTabWidget(self)
        self._sections.setObjectName("SettingsSections")
        self._sections.setDocumentMode(True)
        self._sections.addTab(
            self._settings_section_page(g_behavior, g_theme, g_rime, g_start), "常规",
        )
        self._sections.addTab(
            self._settings_section_page(
                g_files, g_extensions, g_schema, g_library,
            ),
            "词典与文件",
        )
        self._sections.addTab(
            self._settings_section_page(g_backup), "备份与恢复",
        )
        self._sections.addTab(
            self._settings_section_page(g_diagnostics, g_wanxiang_update, g_github), "诊断与更新",
        )
        outer.addWidget(self._sections)

    def _settings_section_page(self, *groups: QGroupBox) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget(scroll)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(12)
        for group in groups:
            layout.addWidget(group)
        layout.addStretch(1)
        scroll.setWidget(content)
        return scroll

    # ------------------------------------------------------------------ #
    def set_schema_repo(self, repo: SchemaRepo) -> None:
        self._schema_repo = repo
        self.refresh()

    def refresh(self) -> None:
        self._rime_dir.setText(self._settings.rime_dir)
        policy = self._scheme_backup_policy()
        self._backup.keep = policy["backup_count"]
        self._backup.auto_cleanup = bool(getattr(self._settings, "backup_auto_cleanup", True))
        self._backup.backup_dir = getattr(self._settings, "backup_dir", "")
        if getattr(sys, "frozen", False) and self._backup.using_default_backup_dir:
            self._backup_path.setText(r"默认：用户文档\RIME 配置小工具\Backups")
            self._backup_path.setToolTip(str(self._backup.backup_dir))
        else:
            self._backup_path.setText(str(self._backup.backup_dir))
            self._backup_path.setToolTip("")
        self._btn_backup_default.setEnabled(bool(getattr(self._settings, "backup_dir", "")))
        self._refresh_restore_file_choices()
        self._refresh_backup_versions()
        self._cb_autodeploy.setChecked(self._settings.auto_deploy)
        self._cb_hotkey.setChecked(self._settings.hotkey_enabled)
        self._hotkey_seq.setKeySequence(QKeySequence(self._settings.hotkey_combo))

        # #6 部署器路径自动回填：探测到则写回设置并刷新输入框
        if not self._settings.deployer_path and self._deploy.deployer_path:
            self._settings.deployer_path = self._deploy.deployer_path
        self._deploy_path.setText(self._settings.deployer_path)

        # #7 备份份数下拉定位（1~5 直接选中；其余记为自定义）
        bc = policy["backup_count"]
        idx = self._backup_combo.findText(str(bc))
        if idx >= 0:
            self._backup_combo.setCurrentIndex(idx)
        else:
            self._backup_combo.setCurrentText("自定义…")

        self._cb_sandbox.blockSignals(True)
        try:
            self._cb_sandbox.setChecked(self._settings.sandbox_mode)
        finally:
            self._cb_sandbox.blockSignals(False)
        self._pending_backup_settings = {
            "backup_count": policy["backup_count"],
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
        self.sync_autostart_state()

        # 主题：按内部值 light/dark/ink 定位，仅刷新卡片选中态，不触发切换信号。
        self._set_theme_cards(self._settings.theme)
        self._cb_wanxiang_check.setChecked(bool(self._settings_get("wanxiang_auto_check", True)))
        self._cb_wanxiang_update.setChecked(bool(self._settings_get("wanxiang_auto_update", False)))
        saved_wanxiang_state = str(self._settings_get("wanxiang_update_state", ""))
        if saved_wanxiang_state:
            self._lbl_wanxiang_update.setText(saved_wanxiang_state)
        self._refresh_wanxiang_recovery_status()

        self._refresh_schema_info()
        self._schedule_dictionary_refresh()
        self._refresh_managed_files()

        # 部署可用性；沙盒模式下禁止触发真实部署。
        if self._settings.sandbox_mode:
            self._lbl_deploy.setText("沙盒模式：不会触发真实部署。")
            self._btn_deploy.setEnabled(False)
        elif self._deploy.available:
            self._lbl_deploy.setText(f"重新部署程序：{self._deploy.deployer_path}")
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
        managed = self._managed_scheme_files()
        mapping = {
            "phrase": managed.phrase_filename,
            "schema": managed.schema_filename,
            "symbols": managed.symbols_filename,
        }
        for key, filename in mapping.items():
            file_path = base / filename if base else Path(filename)
            label = self._managed_labels[key]
            label.setText(self._file_summary(file_path, mode))
            self._managed_open[key].setEnabled(file_path.is_file())

    def _open_path(self, path: Path) -> None:
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_managed_file(self, key: str) -> None:
        base = self._active_rime_dir()
        managed = self._managed_scheme_files()
        filename = {
            "phrase": managed.phrase_filename,
            "schema": managed.schema_filename,
            "symbols": managed.symbols_filename,
        }.get(key, key)
        if base:
            self._open_path(base / filename)

    def _open_dictionary_center(self, scope: str) -> None:
        if self._dictionary_center_opener is not None:
            self._dictionary_center_opener(scope)

    def _open_extension_file(self, key: str) -> None:
        path = self._extension_paths.get(key)
        if path:
            self._open_path(path)

    def _schedule_dictionary_refresh(self) -> None:
        base = self._active_rime_dir()
        key = self._dictionary_signature(base)
        if key == self._dictionary_refresh_key:
            return
        self._dictionary_refresh_key = key
        self._dictionary_generation += 1
        generation = self._dictionary_generation
        self._dictionary_catalog_label.setText("正在后台读取当前方案词典…")
        self._extension_catalog_label.setText("正在后台读取扩展词典…")
        self._dictionary_catalog_button.setEnabled(False)
        self._extension_catalog_button.setEnabled(False)
        self._dictionary_poll.start()

        def deliver(snapshot, error) -> None:
            self._dictionary_results.put((
                generation,
                snapshot if error is None else None,
                "" if error is None else str(error),
            ))

        if self._data_engine_client is not None:
            self._data_engine_client.request_latest(
                "dictionary_snapshot", {"rime_dir": str(base or "")}, deliver,
            )
            return

        def work() -> None:
            try:
                snapshot = self._build_dictionary_snapshot(base)
                self._dictionary_results.put((generation, snapshot, ""))
            except Exception as exc:
                self._dictionary_results.put((generation, None, str(exc)))

        threading.Thread(
            target=work, daemon=True, name="settings-dictionary-catalog",
        ).start()

    def _drain_dictionary_results(self) -> None:
        latest = None
        while True:
            try:
                latest = self._dictionary_results.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        generation, snapshot, error = latest
        if generation != self._dictionary_generation:
            return
        self._dictionary_poll.stop()
        if snapshot is None:
            message = f"词典信息读取失败：{error}"
            self._dictionary_catalog_label.setText(message)
            self._extension_catalog_label.setText(message)
            return
        self._apply_dictionary_snapshot(snapshot)

    @staticmethod
    def _dictionary_signature(base: Path | None) -> tuple:
        return dictionary_catalog_signature(base)

    @staticmethod
    def _build_dictionary_snapshot(base: Path | None) -> dict:
        return dictionary_catalog_snapshot(base)

    def _apply_dictionary_snapshot(self, snapshot: dict) -> None:
        schemes = tuple(snapshot["schemes"])
        catalogs = snapshot["catalogs"]
        enabled = bool(schemes and self._dictionary_center_opener is not None)
        self._dictionary_catalog_button.setEnabled(enabled)
        self._extension_catalog_button.setEnabled(enabled)
        if not schemes:
            message = "未识别当前启用方案；不会混合显示其他已安装词库。"
            self._dictionary_catalog_label.setText(message)
            self._extension_catalog_label.setText(message)
        elif len(schemes) == 1:
            scheme_id = schemes[0]
            catalog = catalogs[scheme_id]
            name = scheme_title(scheme_id)
            self._dictionary_catalog_label.setText(
                f"当前方案：{name} · {catalog['total']} 个词典文件或入口"
            )
            self._extension_catalog_label.setText(
                f"当前方案：{name} · {catalog['extensions']} 个扩展/个人/学习词典"
            )
        else:
            names = "、".join(scheme_title(item) for item in schemes)
            self._dictionary_catalog_label.setText(
                f"已启用多个方案：{names}；打开后单独选择，绝不混合。"
            )
            self._extension_catalog_label.setText(
                "已启用多个方案；打开后按方案查看扩展词典。"
            )

        for key, paths in snapshot["extensions"].items():
            if not paths:
                self._extension_paths.pop(key, None)
                self._extension_labels[key].setText("未找到")
                self._extension_open[key].setEnabled(False)
                continue
            first_path = Path(paths[0])
            self._extension_paths[key] = first_path
            self._extension_open[key].setEnabled(True)
            suffix = f" 等 {len(paths)} 个文件" if len(paths) > 1 else ""
            self._extension_labels[key].setText(
                f"已检测 · 只读{suffix}\n{first_path}"
            )

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
            policy = self._scheme_backup_policy()
            base = datetime.fromisoformat(str(policy.get("last_backup_at", "")))
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
        for key in ("scheduled_backup_enabled", "backup_interval_days", "backup_auto_cleanup"):
            self._settings.set(key, pending[key])
        self._set_scheme_backup_policy(backup_count=pending["backup_count"])
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
        resolution = self._scheme_resolution()
        if resolution is not None and not resolution.writable:
            message = f"备份已停止：{resolution.reason}。"
            self._lbl_backup_status.setProperty("role", "error")
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText(message)
            return False, message
        schema_id = self._current_schema_id()
        policy = self._scheme_backup_policy()
        self._backup.keep = policy["backup_count"]
        self._backup.auto_cleanup = bool(getattr(self._settings, "backup_auto_cleanup", True))
        files = list(self._managed_scheme_files().backup_files())
        report = self._backup.backup_files_report(files)
        saved = sum(path is not None for path in report.saved.values())
        removed = len(report.removed)
        if not saved:
            message = "当前输入方案没有可备份的文件，未创建备份。"
            self._lbl_backup_status.setProperty("role", "warning")
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText(message)
            return False, message
        now = datetime.now().isoformat(timespec="seconds")
        self._set_scheme_backup_policy(last_backup_at=now)
        message = f"已备份当前输入方案的 {saved} 个文件。"
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

    def _refresh_restore_file_choices(self) -> None:
        if not hasattr(self, "_restore_file"):
            return
        managed = self._managed_scheme_files()
        previous = self._restore_file.currentData()
        choices = (
            (managed.phrase_filename, "个人词库"),
            (managed.group_filename, "词库分组"),
            (managed.schema_filename, "输入方案"),
            (managed.symbols_filename, "符号表"),
            (managed.display_filename, "拼音显示"),
        )
        self._restore_file.blockSignals(True)
        self._restore_file.clear()
        for filename, purpose in choices:
            self._restore_file.addItem(purpose, filename)
        index = self._restore_file.findData(previous)
        self._restore_file.setCurrentIndex(max(0, index))
        self._restore_file.blockSignals(False)

    def _on_backup_now(self) -> None:
        self.perform_managed_backup()

    def _on_check_update(self) -> None:
        self._btn_check_update.setEnabled(False)
        self._lbl_update_status.setProperty("role", "info")
        self._lbl_update_status.setVisible(True)
        self._lbl_update_status.setText("正在检查更新…")
        self._application_update_progress.start("正在连接 GitHub 并检查最新版本…")
        self._update_poll.start()
        threading.Thread(
            target=self._check_update_worker,
            name="rime-config-update",
            daemon=True,
        ).start()

    def _maybe_check_wanxiang(self) -> None:
        if self._disposed or not self._background_tasks_enabled:
            return
        if self._wanxiang_update_service_provider is None \
                or not bool(self._settings_get("wanxiang_auto_check", True)):
            return
        try:
            last = datetime.fromisoformat(str(self._settings_get("wanxiang_last_check", "")))
            if datetime.now() - last < timedelta(days=1):
                return
        except ValueError:
            pass
        self._start_wanxiang_check()

    def _start_wanxiang_check(self) -> None:
        if self._wanxiang_update_service_provider is None:
            self._lbl_wanxiang_update.setText("当前环境未启用万象更新服务。")
            return
        self._btn_wanxiang_check.setEnabled(False)
        self._lbl_wanxiang_update.setText("正在检查万象更新…")
        self._wanxiang_poll.start()

        def worker() -> None:
            try:
                report = self._wanxiang_update_service_provider().check()
                self._wanxiang_results.put(("check", report, None))
            except Exception as exc:
                self._wanxiang_results.put(("check", None, exc))

        threading.Thread(target=worker, name="wanxiang-update-check", daemon=True).start()

    def _start_wanxiang_update(
        self, *, force: bool = False, automatic: bool = False,
    ) -> None:
        if self._wanxiang_report is None or self._wanxiang_update_service_provider is None:
            return
        confirmation = WanxiangUpdateConfirmationDialog(
            self._wanxiang_report,
            force=force,
            automatic=automatic,
            parent=self,
        )
        if confirmation.exec() != QDialog.DialogCode.Accepted:
            return
        self._btn_wanxiang_update.setEnabled(False)
        self._btn_wanxiang_force.setEnabled(False)
        self._lbl_wanxiang_update.setText(
            "正在建立独立备份并安装万象更新…" if force
            else "正在下载并检查更新是否可以安全安装…"
        )
        self._wanxiang_poll.start()

        def worker() -> None:
            try:
                result = self._wanxiang_update_service_provider().update(
                    self._wanxiang_report, force=force,
                )
                self._wanxiang_results.put(("update", result, None))
            except Exception as exc:
                self._wanxiang_results.put(("update", None, exc))

        threading.Thread(target=worker, name="wanxiang-safe-update", daemon=True).start()

    def _start_wanxiang_restore(self) -> None:
        if self._wanxiang_update_service_provider is None:
            return
        answer = QMessageBox.question(
            self,
            "恢复万象更新",
            "将恢复最近一次万象更新前的独立备份，并在恢复后重新部署。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._btn_wanxiang_restore.setEnabled(False)
        self._lbl_wanxiang_update.setText("正在恢复万象更新前版本…")
        self._wanxiang_poll.start()

        def worker() -> None:
            try:
                result = self._wanxiang_update_service_provider().rollback_last_update()
                self._wanxiang_results.put(("restore", result, None))
            except Exception as exc:
                self._wanxiang_results.put(("restore", None, exc))

        threading.Thread(target=worker, name="wanxiang-update-restore", daemon=True).start()

    def _refresh_wanxiang_recovery_status(self) -> None:
        if self._wanxiang_update_service_provider is None:
            self._btn_wanxiang_restore.setEnabled(False)
            self._lbl_wanxiang_recovery.setText("当前环境未启用万象更新备份。")
            return
        try:
            service = self._wanxiang_update_service_provider()
            available = service.has_recovery_backup()
            self._btn_wanxiang_restore.setEnabled(available)
            self._lbl_wanxiang_recovery.setText(service.recovery_summary())
        except Exception:
            self._btn_wanxiang_restore.setEnabled(False)
            self._lbl_wanxiang_recovery.setText("暂时无法读取独立更新备份。")

    def _drain_wanxiang_result(self) -> None:
        try:
            operation, result, error = self._wanxiang_results.get_nowait()
        except queue.Empty:
            return
        self._wanxiang_poll.stop()
        self._btn_wanxiang_check.setEnabled(True)
        if error is not None:
            self._lbl_wanxiang_update.setText(f"万象更新失败：{error}")
            self._refresh_wanxiang_recovery_status()
            return
        if operation == "restore":
            ok, message = result
            self._lbl_wanxiang_update.setText(message)
            self._settings.set("wanxiang_update_state", message)
            self._refresh_wanxiang_recovery_status()
            if ok:
                self._wanxiang_deploy_pending = True
                self._wanxiang_deploy_mode = "restore"
                queued = self._queue_wanxiang_deploy("正在部署恢复后的万象文件…")
                if not queued and not bool(getattr(self._settings, "sandbox_mode", False)):
                    self._wanxiang_deploy_pending = False
                    self._wanxiang_deploy_mode = ""
                    message = f"{message} 但重新部署未能启动，请手动重新部署。"
                    self._lbl_wanxiang_update.setText(message)
                    self._settings.set("wanxiang_update_state", message)
            return
        if operation == "update":
            ok, message = result
            self._lbl_wanxiang_update.setText(message)
            self._settings.set("wanxiang_update_state", message)
            self._refresh_wanxiang_recovery_status()
            can_retry = bool(
                self._wanxiang_report
                and self._wanxiang_report.compatible
                and self._wanxiang_report.download_url
                and self._wanxiang_report.new_version != self._wanxiang_report.current_version
            )
            self._btn_wanxiang_update.setEnabled(not ok and can_retry)
            self._wanxiang_force_available = (not ok and message.startswith("需要强制更新："))
            self._btn_wanxiang_force.setVisible(self._wanxiang_force_available)
            self._btn_wanxiang_force.setEnabled(self._wanxiang_force_available)
            if ok:
                self._btn_wanxiang_force.setVisible(False)
                self._wanxiang_deploy_pending = True
                self._wanxiang_deploy_mode = "update"
                queued = self._queue_wanxiang_deploy("正在重新部署并重建万象词典…")
                if not queued and not bool(getattr(self._settings, "sandbox_mode", False)):
                    rollback_ok, rollback_message = (
                        self._wanxiang_update_service_provider().rollback_last_update()
                    )
                    self._wanxiang_deploy_pending = False
                    self._wanxiang_deploy_mode = ""
                    message = f"{message} 部署未能启动；{rollback_message}"
                    self._lbl_wanxiang_update.setText(message)
                    self._settings.set("wanxiang_update_state", message)
                    if rollback_ok:
                        self.wanxiangFilesChanged.emit()
                    self._refresh_wanxiang_recovery_status()
                    return
            return
        report = result
        self._wanxiang_report = report
        self._wanxiang_force_available = False
        self._btn_wanxiang_force.setVisible(False)
        self._btn_wanxiang_info.setEnabled(True)
        self._settings.set("wanxiang_last_check", datetime.now().isoformat(timespec="seconds"))
        detail = (
            f"当前万象：{report.current_version or '未知'}；发现版本：{report.new_version or '未知'}；"
            f"RimeConfig：{report.tool_version}；{report.reason}"
        )
        self._wanxiang_status.set_report(report)
        self._settings.set("wanxiang_update_state", detail)
        can_update = bool(report.compatible and report.download_url
                          and report.new_version != report.current_version)
        self._btn_wanxiang_update.setEnabled(can_update)
        if (can_update and not bool(getattr(report, "requires_confirmation", False))
                and bool(self._settings_get("wanxiang_auto_update", False))):
            self._start_wanxiang_update(force=False, automatic=True)
            return
        if report.waiting_for_adapter:
            if str(self._settings_get("wanxiang_dismissed_version", "")) == report.new_version:
                return
            try:
                remind_after = datetime.fromisoformat(
                    str(self._settings_get("wanxiang_remind_after", ""))
                )
                if datetime.now() < remind_after:
                    return
            except ValueError:
                pass
            dialog = WanxiangCompatibilityDialog(report, self)
            result = dialog.exec()
            if result == WanxiangCompatibilityDialog.LATER:
                self._settings.set(
                    "wanxiang_remind_after",
                    (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"),
                )
            elif result == WanxiangCompatibilityDialog.DISMISS:
                self._settings.set("wanxiang_dismissed_version", report.new_version)

    def handle_wanxiang_deploy_result(self, ok: bool, message: str) -> None:
        """Rollback a just-installed Wanxiang update if Weasel rejects it."""
        if not self._wanxiang_deploy_pending:
            return
        mode = self._wanxiang_deploy_mode
        self._wanxiang_deploy_pending = False
        self._wanxiang_deploy_mode = ""
        service = self._wanxiang_update_service_provider()
        if ok:
            artifacts_ok, artifacts_message = service.validate_deployment()
            ok = artifacts_ok
            if not artifacts_ok:
                message = artifacts_message
        if ok and mode == "update":
            _confirmed, backup_message = (
                service.confirm_update()
            )
            text = "小狼毫服务已恢复，并已生成万象构建产物。"
            if backup_message:
                text = f"{text} {backup_message}"
            self._lbl_wanxiang_update.setText(text)
            self._settings.set("wanxiang_update_state", text)
            self._refresh_wanxiang_recovery_status()
            self.wanxiangFilesChanged.emit()
            return
        if ok and mode in {"restore", "recovery"}:
            text = "万象文件已恢复，重新部署和构建产物检查均已完成。"
            self._lbl_wanxiang_update.setText(text)
            self._settings.set("wanxiang_update_state", text)
            self._refresh_wanxiang_recovery_status()
            self.wanxiangFilesChanged.emit()
            return
        if mode in {"restore", "recovery"}:
            text = f"万象恢复后的重新部署失败：{message}"
            self._lbl_wanxiang_update.setText(text)
            self._settings.set("wanxiang_update_state", text)
            self._refresh_wanxiang_recovery_status()
            self.wanxiangFilesChanged.emit()
            return
        rollback_ok, rollback_message = service.rollback_last_update()
        text = f"万象更新部署失败：{message}；{rollback_message}"
        self._lbl_wanxiang_update.setText(text)
        self._settings.set("wanxiang_update_state", text)
        self.wanxiangFilesChanged.emit()
        self._refresh_wanxiang_recovery_status()
        if rollback_ok:
            self._wanxiang_deploy_pending = True
            self._wanxiang_deploy_mode = "recovery"
            if not self._queue_wanxiang_deploy("更新未能构建，正在恢复并重新部署原版本…"):
                self._wanxiang_deploy_pending = False
                self._wanxiang_deploy_mode = ""

    def _queue_wanxiang_deploy(self, phase: str) -> bool:
        callback = self._wanxiang_deploy_request
        if callback is None:
            return False
        try:
            return bool(callback(phase))
        except TypeError:
            return bool(callback())

    def _check_update_worker(self) -> None:
        try:
            service = UpdateService()
            release, message = service.check(__version__)
            if release is None:
                result = ("result", False, message, False)
            else:
                self._update_results.put((
                    "phase",
                    f"发现 {release.version}，正在下载并校验更新包…",
                ))
                ok, message = service.download_replace_and_restart(
                    release, current_version=__version__,
                )
                result = ("result", ok, message, ok)
        except Exception as exc:
            result = ("result", False, f"检查更新失败：{exc}", False)
        self._update_results.put(result)

    def _drain_update_results(self) -> None:
        while True:
            try:
                result = self._update_results.get_nowait()
            except queue.Empty:
                return
            if result[0] == "phase":
                self._application_update_progress.set_phase(result[1])
                self._lbl_update_status.setText(result[1])
                continue
            self._update_poll.stop()
            self._on_update_finished(*result[1:])
            return

    def _on_update_finished(self, ok: bool, message: str, restart: bool) -> None:
        self._lbl_update_status.setProperty("role", "success" if ok else "warning")
        self._lbl_update_status.setVisible(True)
        self._lbl_update_status.setText(message)
        if restart:
            from PySide6.QtWidgets import QApplication
            self._application_update_progress.set_phase(
                "更新器已接管，正在关闭旧版本并自动重启…"
            )
            QTimer.singleShot(700, QApplication.quit)
            return
        self._application_update_progress.finish()
        self._btn_check_update.setEnabled(True)
        if message.startswith("已是最新版本"):
            QMessageBox.information(self, "检查更新", message)
        elif not ok:
            QMessageBox.warning(self, "更新未完成", message)

    def _on_deploy_path_changed(self) -> None:
        value = self._deploy_path.text().strip()
        path = Path(value) if value else None
        if path is not None and (not path.is_file() or path.name.casefold() != "weaseldeployer.exe"):
            self._lbl_deploy.setProperty("role", "error")
            self._lbl_deploy.setText("路径无效：请选择 WeaselDeployer.exe；原设置未改变。")
            self._deploy_path.setText(self._settings.deployer_path)
            self._deploy_path.setReadOnly(True)
            return
        self._settings.deployer_path = value
        # 重新探测部署器可用性（手动路径优先）
        self._deploy.redetect()
        self._deploy_path.setReadOnly(True)
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
            self._btn_restore_preview.setEnabled(False)
            return
        for path in self._backup.list_backups(str(filename)):
            self._restore_version.addItem(path.name, str(path))
        available = self._restore_version.count() > 0
        self._btn_restore.setEnabled(available)
        self._btn_restore_preview.setEnabled(available)

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
        phrase_filename = self._managed_scheme_files().phrase_filename
        display_filename = self._managed_scheme_files().display_filename
        if filename == phrase_filename:
            try:
                selected_mtime = Path(backup_path).stat().st_mtime
                candidates = self._backup.list_backups(display_filename)
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
        if filename == phrase_filename:
            self._backup.backup(display_filename)
        ok = self._backup.restore(backup_path, filename)
        if ok and companion_backup is not None:
            self._backup.restore(str(companion_backup), display_filename)
        if ok:
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText(f"已恢复：{filename}")
            self.rimeDirChanged.emit(self._settings.rime_dir)
            self._refresh_backup_versions(force=True)
        else:
            self._lbl_backup_status.setVisible(True)
            self._lbl_backup_status.setText("恢复失败：备份文件不存在或无法读取。")

    def _preview_restore_backup(self) -> None:
        filename = str(self._restore_file.currentData() or "")
        backup_value = str(self._restore_version.currentData() or "")
        if not filename or not backup_value:
            return
        current = Path(self._settings.rime_dir) / filename
        backup = Path(backup_value)
        try:
            RestorePreviewDialog(current, backup, filename, self).exec()
        except OSError as exc:
            QMessageBox.warning(self, "无法比较", str(exc))

    def _on_browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(self, "选择 Rime 目录",
                                             self._settings.rime_dir or "")
        if d:
            self._rime_dir.setText(d)
            self._apply_rime_dir()

    def _apply_rime_dir(self) -> None:
        value = self._rime_dir.text().strip()
        path = Path(value) if value else Path()
        markers = (
            path / "user.yaml", path / "default.yaml", path / "default.custom.yaml",
            path / "build" / "default.yaml",
        )
        if not value or not path.is_dir() or not any(marker.is_file() for marker in markers):
            self._lbl_deploy.setProperty("role", "error")
            self._lbl_deploy.setText("目录无效：未找到 Rime 的 user/default 配置；原设置未改变。")
            self._rime_dir.setText(self._settings.rime_dir)
            self._rime_dir.setReadOnly(True)
            return
        if value == self._settings.rime_dir:
            self._rime_dir.setReadOnly(True)
            return
        self._settings.rime_dir = value
        self._rime_dir.setReadOnly(True)
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


    def sync_autostart_state(self) -> bool:
        """Refresh the checkbox from the validated Windows startup entry."""
        status_getter = getattr(self._autostart, "status", None)
        status = status_getter() if callable(status_getter) else None
        enabled = bool(status.enabled) if status is not None else bool(self._autostart.enabled)
        reason = str(getattr(status, "reason", ""))
        self._cb_autostart.blockSignals(True)
        try:
            self._cb_autostart.setChecked(enabled)
            self._cb_autostart.setToolTip(reason)
        finally:
            self._cb_autostart.blockSignals(False)
        self._set_autostart_status_text(enabled, reason)
        self._settings.autostart = enabled
        return enabled

    def _set_autostart_status_text(self, enabled: bool, reason: str) -> None:
        """Show the actual Windows startup approval state beside the checkbox."""
        normalized = reason.strip()
        if enabled:
            text, role = "Windows 启动项：已启用", "success"
        elif normalized == "Windows 已禁用此启动项":
            text, role = "Windows 启动项：已被系统禁用", "warning"
        elif normalized and normalized != "未启用":
            text, role = f"Windows 启动项：{normalized}", "warning"
        else:
            text, role = "Windows 启动项：未启用", "neutral"
        self._lbl_autostart_status.setText(text)
        self._lbl_autostart_status.setToolTip(normalized or text)
        if self._lbl_autostart_status.property("role") != role:
            self._lbl_autostart_status.setProperty("role", role)
            style = self._lbl_autostart_status.style()
            style.unpolish(self._lbl_autostart_status)
            style.polish(self._lbl_autostart_status)

    def _on_autostart_toggled(self, checked: bool) -> None:
        if checked:
            ok = self._autostart.enable()
            if not ok:
                # 启用失败：回退勾选时不能再次触发系统操作。
                self._cb_autostart.blockSignals(True)
                try:
                    self._cb_autostart.setChecked(False)
                finally:
                    self._cb_autostart.blockSignals(False)
                self._settings.autostart = False
                QMessageBox.warning(
                    self, "开机自启",
                    "启用开机自启失败（可能缺少权限或 pywin32）。\n"
                    "可在系统「启动」文件夹手动创建本程序快捷方式。")
                return
            self._settings.autostart = True
        else:
            if not self._autostart.disable():
                self.sync_autostart_state()
                QMessageBox.warning(self, "开机自启", "关闭开机自启失败，请检查启动文件夹权限。")
                return
            self._settings.autostart = False
        # The inline status beside the checkbox confirms the final Windows state
        # without showing a modal dialog that can block the settings page.
        self.sync_autostart_state()

    def _on_deploy(self) -> None:
        if self._deploy_request is not None:
            started = bool(self._deploy_request())
            self._lbl_deploy.setText(
                "正在后台部署…" if started else "部署任务已合并或当前不可用。"
            )
            return
        ok, msg = self._deploy.deploy()
        from PySide6.QtWidgets import QMessageBox

        if ok:
            QMessageBox.information(self, "部署", msg)
        else:
            QMessageBox.warning(self, "部署", msg)
