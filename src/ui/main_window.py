"""主窗口（MainWindow）。

装配六个稳定工作区（左侧导航 + 懒加载堆叠页）：
    词库工作台、词典中心、词库维护、输入习惯、符号管理、设置。
并接线：系统托盘（TrayIcon）、全局热键（HotkeyManager）、开机自启（Autostart）。
加载风格 A 的 application.qss。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
import queue
import threading
from typing import Optional

from src import __version__

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QWidget,
)

from src.app_context import AppContext
from src.ui.phrase_manager import PhraseManager
from src.ui.dictionary_center_dialog import DictionaryCenterDialog
from src.ui.index_progress import present_index_progress
from src.ui.navigation_shell import NavigationShell
from src.ui.settings_widget import SettingsWidget
from src.ui.symbols_config_widget import SymbolsConfigWidget
from src.ui.theme import apply_theme, apply_window_theme, set_ink_decor
from src.ui.tray_icon import TrayIcon
from src.ui.toast_notification import ToastNotification
from src.ui.rime_environment_monitor import RimeEnvironmentMonitor
from src.ui.configuration_pages import (
    InputHabitsWidget,
    LibraryMaintenanceWidget,
)
from src.ui.wanxiang_update_notice import WanxiangCompatibilityDialog
from src.service.rime_patch_service import RimePatchService
from src.service.wanxiang_update_service import WanxiangUpdateService
from src.utils.logger import get_logger

logger = get_logger(__name__)

_QSS_PATH = Path(__file__).resolve().parent / "application.qss"

TAB_LIBRARY = 0
TAB_DICTIONARIES = 1
TAB_MAINTENANCE = 2
TAB_INPUT = 3
TAB_SYMBOLS = 4
TAB_SETTINGS = 5
_TABS = [
    "词库工作台", "词典中心", "词库维护", "输入习惯",
    "符号管理", "设置",
]


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, context: AppContext, parent: Optional[QWidget] = None,
                 start_minimized: bool = False,
                 background_tasks_enabled: bool = True) -> None:
        super().__init__(parent)
        self._ctx = context
        self._start_minimized = start_minimized
        self._background_tasks_enabled = bool(background_tasks_enabled)
        self._disposed = False
        self._optional_services_started = False
        self._attached_rime_dir = context.settings.rime_dir
        self._attached_sandbox_mode = context.settings.sandbox_mode
        self._attached_scheme_id = context.active_scheme_id
        self._base_window_title = f"RIME 配置小工具 v{__version__}"
        self.setWindowTitle(self._base_window_title)
        self.resize(1180, 760)
        self._build_ink_decor()
        self._apply_style()
        self._set_app_icon()

        self._build_pages()
        self._build_index_status()
        self._hide_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._hide_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._hide_shortcut.activated.connect(self.hide)
        if self._background_tasks_enabled:
            self._wire_autostart()
        self._build_tray()
        if self._background_tasks_enabled:
            self._wire_hotkey()
        self._scheduled_backup_timer = QTimer(self)
        self._scheduled_backup_timer.timeout.connect(self._run_scheduled_backup)
        if self._background_tasks_enabled:
            self._scheduled_backup_timer.start(60 * 60 * 1000)
            self._run_scheduled_backup()
        self._environment_monitor = RimeEnvironmentMonitor(
            lambda: self._ctx.settings.rime_dir, self,
        )
        self._environment_monitor.changed.connect(self._on_environment_changed)
        if self._background_tasks_enabled:
            self._environment_monitor.start()
        self._wanxiang_background_results: queue.SimpleQueue = queue.SimpleQueue()
        self._wanxiang_background_poll = QTimer(self)
        self._wanxiang_background_poll.setInterval(200)
        self._wanxiang_background_poll.timeout.connect(
            self._drain_wanxiang_background_result
        )
        self._wanxiang_background_pending_service = None
        if self._background_tasks_enabled:
            QTimer.singleShot(2200, self._maybe_check_wanxiang_background)

        # 应用当前主题（作用于 QApplication，弹窗自动继承）
        self._update_tray_deploy()

        self._select_tab(TAB_LIBRARY)
        self._check_rime_dir()
        # Normal launches warm optional services after first paint. Autostart waits
        # until the user actually opens the window, keeping Windows login light.
        if self._background_tasks_enabled and not self._start_minimized:
            QTimer.singleShot(800, self._start_optional_services)
        if self._background_tasks_enabled and getattr(sys, "frozen", False) \
                and self._ctx.settings.get("antivirus_notice_version", "") != __version__:
            QTimer.singleShot(1400, self._show_first_run_security_notice)
        if not self._background_tasks_enabled:
            self._index_status_timer.stop()

    def _show_first_run_security_notice(self) -> None:
        if self._disposed:
            return
        QMessageBox.information(
            self,
            "新版本首次运行提示",
            "本工具会调用小狼毫的重新部署程序、读取输入法设置，并在本机整理词典数据。"
            "部分杀毒软件可能因此提示风险；请只使用项目正式发布包。",
        )
        self._ctx.settings.set("antivirus_notice_version", __version__)

    def _maybe_check_wanxiang_background(self) -> None:
        if self._disposed or not self._background_tasks_enabled:
            return
        settings = self._ctx.settings
        if settings.sandbox_mode or not bool(settings.get("wanxiang_auto_check", True)):
            return
        rime_dir = settings.rime_dir
        if not rime_dir or not (Path(rime_dir) / "wanxiang.schema.yaml").is_file():
            return
        try:
            last = datetime.fromisoformat(str(settings.get("wanxiang_last_check", "")))
            if datetime.now() - last < timedelta(days=1):
                return
        except ValueError:
            pass
        service = WanxiangUpdateService(
            rime_dir, self._ctx.backup_service, self._ctx.file_transaction_writer,
        )
        self._wanxiang_background_poll.start()

        def worker() -> None:
            try:
                report = service.check()
                if (report.compatible and report.download_url
                        and report.new_version != report.current_version
                        and not bool(getattr(report, "requires_confirmation", False))
                        and bool(settings.get("wanxiang_auto_update", False))):
                    result = service.update(report)
                    self._wanxiang_background_results.put(("update", service, report, result, None))
                else:
                    self._wanxiang_background_results.put(("check", service, report, None, None))
            except Exception as exc:
                self._wanxiang_background_results.put(("check", service, None, None, exc))

        threading.Thread(
            target=worker, name="wanxiang-background-update", daemon=True,
        ).start()

    def _drain_wanxiang_background_result(self) -> None:
        try:
            operation, service, report, result, error = (
                self._wanxiang_background_results.get_nowait()
            )
        except queue.Empty:
            return
        self._wanxiang_background_poll.stop()
        if error is not None:
            self._ctx.settings.set("wanxiang_update_state", f"检查万象更新失败：{error}")
            return
        assert report is not None
        detail = (
            f"当前万象：{report.current_version or '未知'}；发现版本：{report.new_version or '未知'}；"
            f"RimeConfig：{report.tool_version}；{report.reason}"
        )
        self._ctx.settings.set("wanxiang_last_check", datetime.now().isoformat(timespec="seconds"))
        self._ctx.settings.set("wanxiang_update_state", detail)
        if operation == "update":
            ok, message = result
            self._ctx.settings.set("wanxiang_update_state", message)
            if ok:
                self._wanxiang_background_pending_service = service
                if not self._phrase_manager.request_manual_deploy():
                    service.rollback_last_update()
                    self._wanxiang_background_pending_service = None
                self._ctx.rebuild_repos()
                self._reattach_repos()
            return
        if not report.waiting_for_adapter:
            return
        if self._ctx.settings.get("wanxiang_dismissed_version", "") == report.new_version:
            return
        try:
            remind = datetime.fromisoformat(
                str(self._ctx.settings.get("wanxiang_remind_after", ""))
            )
            if datetime.now() < remind:
                return
        except ValueError:
            pass
        dialog = WanxiangCompatibilityDialog(report, self)
        result = dialog.exec()
        if result == WanxiangCompatibilityDialog.LATER:
            self._ctx.settings.set(
                "wanxiang_remind_after",
                (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"),
            )
        elif result == WanxiangCompatibilityDialog.DISMISS:
            self._ctx.settings.set("wanxiang_dismissed_version", report.new_version)

    def _build_index_status(self) -> None:
        """Persistent, non-modal index feedback shared by every workspace."""
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self._index_status_label = QLabel("", self)
        self._index_progress = QProgressBar(self)
        self._index_progress.setRange(0, 100)
        self._index_progress.setTextVisible(True)
        self._index_progress.setFixedWidth(230)
        self._index_action = QPushButton("取消构建", self)
        self._index_action.clicked.connect(self._on_index_action)
        bar.addWidget(self._index_status_label, 1)
        bar.addPermanentWidget(self._index_progress)
        bar.addPermanentWidget(self._index_action)
        bar.hide()
        self._index_status_timer = QTimer(self)
        self._index_status_timer.setInterval(250)
        self._index_status_timer.timeout.connect(self._refresh_index_status)
        self._index_status_timer.start()

    def _refresh_index_status(self) -> None:
        index = self._ctx.system_dictionary_index
        state = str(getattr(index, "state", ""))
        progress = dict(getattr(index, "progress", {}) or {})
        scheme_id = str(getattr(index, "scheme_id", ""))
        scheme = {"wanxiang": "万象", "rime_frost": "白霜"}.get(
            scheme_id, "当前方案"
        )
        bar = self.statusBar()
        if state in {"building", "cancelling"}:
            view = present_index_progress(progress)
            action = "正在取消" if state == "cancelling" else f"{view.phase_label}中"
            detail = f" · {view.current_file}" if view.current_file else ""
            self._index_status_label.setText(
                f"{action}{scheme}索引 · {view.percent}%{detail}"
            )
            self._index_progress.setValue(view.percent)
            self._index_action.setText("正在取消…" if state == "cancelling" else "取消构建")
            self._index_action.setEnabled(state == "building")
            self.setWindowTitle(
                f"{self._base_window_title} · 正在构建{scheme}索引 {view.percent}%"
            )
            bar.show()
            return
        self.setWindowTitle(self._base_window_title)
        if state in {"failed", "cancelled"}:
            self._index_status_label.setText(
                "索引构建已取消。" if state == "cancelled"
                else "索引构建失败；暂时只查询个人词库。"
            )
            self._index_progress.hide()
            self._index_action.setText("重新构建")
            self._index_action.setEnabled(True)
            bar.show()
            return
        self._index_progress.show()
        bar.hide()
        if state == "ready":
            self._index_status_timer.stop()

    def _on_index_action(self) -> None:
        index = self._ctx.system_dictionary_index
        state = str(getattr(index, "state", ""))
        if state == "building":
            index.cancel_build()
        elif state in {"failed", "cancelled", "unavailable"}:
            index.ensure_ready_async()
            self._index_status_timer.start()
        self._refresh_index_status()

    # ------------------------------------------------------------------ #
    # 样式
    # ------------------------------------------------------------------ #
    def _apply_style(self) -> None:
        try:
            apply_theme(self._ctx.settings.theme)
            apply_window_theme(self, self._ctx.settings.theme)
        except Exception as exc:
            logger.warning("加载 QSS 失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 水墨装饰（常驻隐藏，仅 ink 主题显隐；不在 layout 内，零抖动）
    # ------------------------------------------------------------------ #
    def _build_ink_decor(self) -> None:
        """创建常驻隐藏的水墨装饰：左侧 3px 渐变条 + 朱砂印章。

        - 绝对定位贴左缘/左上角，不参与任何 layout，主题切换不抖动；
        - 默认隐藏，由 ``theme.apply_theme('ink')`` 控制显隐；
        - 渐变与印章底色按设计稿硬编码（朱砂 #B23A2E → 黛青 #2F5D50）。
        """
        from PySide6.QtCore import Qt

        # 左侧 3px 渐变条：朱砂 → 黛青
        decor = QFrame(self)
        decor.setObjectName("InkDecor")
        decor.setFixedWidth(3)
        decor.setStyleSheet(
            "QFrame#InkDecor{background:qlineargradient("
            "x1:0,y1:0,x2:0,y2:1,stop:0 #B23A2E,stop:1 #2F5D50);"
            "border:none;}"
        )
        decor.setHidden(True)
        self._ink_decor = decor

        # 朱砂印章：字「藏」，宋体栈，圆角 6
        seal = QLabel("藏", self)
        seal.setObjectName("InkSeal")
        seal.setFixedSize(34, 34)
        seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seal.setStyleSheet(
            "QLabel#InkSeal{background:#B23A2E;color:#FFFFFF;"
            "border-radius:6px;"
            "font-family:\"Source Han Serif SC\",\"Noto Serif SC\",\"SimSun\",serif;"
            "font-size:20px;font-weight:bold;}"
        )
        seal.setHidden(True)
        self._ink_seal = seal

        # 注册到 theme 模块，供 apply_theme 显隐
        set_ink_decor(self._ink_decor, self._ink_seal)
        # 初始贴边定位（resizeEvent 会继续保持）
        self._position_ink_decor()

    def _position_ink_decor(self) -> None:
        """装饰控件绝对定位：渐变条贴左缘满高，印章默认左上角 (10,10)。"""
        decor = getattr(self, "_ink_decor", None)
        seal = getattr(self, "_ink_seal", None)
        if decor is None or seal is None:
            return
        height = self.height()
        decor.setGeometry(0, 0, 3, height)
        # 印章默认左上角 (10,10)；最终位置以 Windows 实测目检为准
        seal.setGeometry(10, 10, 34, 34)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_ink_decor()

    # ------------------------------------------------------------------ #
    # 应用图标（优先使用随包 ICO；程序化图标仅作资源缺失时的后备）
    # ------------------------------------------------------------------ #
    def _set_app_icon(self) -> None:
        from PySide6.QtGui import QIcon

        icon_path = self._app_icon_path()
        icon = QIcon(str(icon_path)) if icon_path is not None else QIcon()
        if icon.isNull():
            icon = self._make_default_icon()
        QApplication.instance().setWindowIcon(icon)
        self.setWindowIcon(icon)
        self._app_icon = icon

    @staticmethod
    def _app_icon_path() -> Path | None:
        """Find the bundled ICO without depending on the launch directory."""
        bundle_root = getattr(sys, "_MEIPASS", None)
        candidates = []
        if bundle_root:
            candidates.append(Path(bundle_root) / "assets" / "app.ico")
        candidates.append(Path(__file__).resolve().parents[2] / "assets" / "app.ico")
        return next((path for path in candidates if path.is_file()), None)

    def _make_default_icon(self) -> "QIcon":
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

        icon = QIcon()
        for size in (16, 20, 24, 32, 48, 64, 128, 256):
            pm = QPixmap(size, size)
            pm.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            margin = max(1, round(size * 0.09))
            painter.setBrush(QColor("#185FA5"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                margin, margin, size - margin * 2, size - margin * 2, max(3, round(size * 0.22)), max(3, round(size * 0.22)),
            )
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(QFont("Microsoft YaHei UI", max(8, round(size * 0.53)), QFont.Weight.Bold))
            painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "R")
            painter.end()
            icon.addPixmap(pm)
        return icon

    # ------------------------------------------------------------------ #
    # 左侧主导航 + 堆叠页
    # ------------------------------------------------------------------ #
    def _build_pages(self) -> None:
        ctx = self._ctx
        # NavigationShell deliberately preserves the tiny tab-like API used by
        # the existing page lifecycle, while presenting a stable sidebar.
        self._tabs = NavigationShell(self)

        self._phrase_manager = PhraseManager(
            ctx.phrase_repo, ctx.group_service, ctx.backup_service,
            ctx.settings, ctx.deploy_service, ctx.pinyin_service,
            ctx.system_dictionary_index, ctx.rime_preview_service, ctx.metadata_store,
            data_engine_client=ctx.data_engine_client,
            transaction_writer=ctx.file_transaction_writer,
            code_alphabet=ctx.schema_repo.speller_alphabet(),
        )
        # Infrequently used pages are created on first activation. This keeps
        # the first usable window focused on the word library.
        self._symbols_widget: SymbolsConfigWidget | None = None
        self._settings_widget: SettingsWidget | None = None
        self._dictionary_widget: DictionaryCenterDialog | None = None
        self._maintenance_widget = LibraryMaintenanceWidget(self._phrase_manager, self._tabs)
        self._input_widget: InputHabitsWidget | None = None
        self._dictionary_scope = "all"
        self._dictionary_placeholder = QWidget()
        self._input_placeholder = QWidget()
        self._symbols_placeholder = QWidget()
        self._settings_placeholder = QWidget()
        self._phrase_manager.favoriteCompleted.connect(self._on_favorite_completed)
        self._phrase_manager.editCompleted.connect(self._on_edit_completed)
        self._phrase_manager.manualDeploymentCompleted.connect(
            self._on_manual_deployment_completed
        )

        self._tabs.addTab(self._phrase_manager, _TABS[TAB_LIBRARY])
        self._tabs.addTab(self._dictionary_placeholder, _TABS[TAB_DICTIONARIES])
        self._tabs.addTab(self._maintenance_widget, _TABS[TAB_MAINTENANCE])
        self._tabs.addTab(self._input_placeholder, _TABS[TAB_INPUT])
        self._tabs.addTab(self._symbols_placeholder, _TABS[TAB_SYMBOLS])
        self._tabs.addTab(self._settings_placeholder, _TABS[TAB_SETTINGS])
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._tab_activation_generation = 0

        self.setCentralWidget(self._tabs)
        self._phrase_manager.hide_legacy_tools()

    # ------------------------------------------------------------------ #
    # 托盘
    # ------------------------------------------------------------------ #
    def _build_tray(self) -> None:
        self._tray = TrayIcon(self)
        # 使用主窗口初始化的应用图标（程序化生成，托盘必有图标）
        self._tray.set_icon(getattr(self, "_app_icon", QApplication.windowIcon()))
        self._tray.action_open.triggered.connect(self._request_show_main)
        self._tray.requestOpen.connect(self._request_show_main)
        self._tray.action_deploy.triggered.connect(self.deploy_now)
        self._tray.action_auto_deploy.triggered.connect(self._toggle_auto_deploy_from_tray)
        self._tray.action_hotkey.triggered.connect(self._toggle_hotkey_from_tray)
        self._tray.action_autostart.triggered.connect(self._toggle_autostart_from_tray)
        self._tray.action_settings.triggered.connect(self._open_settings_from_tray)
        self._tray.action_quit.triggered.connect(self.quit_app)
        self._tray.set_hotkey_state(self._ctx.settings.hotkey_enabled)
        self._tray.set_auto_deploy_state(self._ctx.settings.auto_deploy)
        self._sync_autostart_state()
        self._tray.show()

    # ------------------------------------------------------------------ #
    # 热键
    # ------------------------------------------------------------------ #
    def _wire_hotkey(self) -> None:
        if not self._ctx.settings.hotkey_enabled:
            self._tray.set_hotkey_state(False)
            return
        if not self._ctx.settings.is_rime_dir_valid():
            self._tray.set_hotkey_state(False)
            logger.info("Rime 目录无效，热键暂不注册。")
            return
        self._register_hotkey()

    def _register_hotkey(self) -> bool:
        if not self._ctx.settings.is_rime_dir_valid():
            self._tray.set_hotkey_state(False)
            logger.info("Rime 目录无效，热键暂不注册。")
            return False
        ok = self._ctx.hotkey_manager.register(
            self._ctx.settings.hotkey_combo, self._on_hotkey_trigger
        )
        self._tray.set_hotkey_state(ok)
        if not ok:
            logger.info("热键未注册（后端不可用）。")
        return ok

    def _on_hotkey_trigger(self, text: str) -> None:
        prepare = getattr(self, "_prepare_scheme_write", None)
        if prepare is not None and not prepare():
            self._show_result_bubble(
                False,
                "无法安全确认当前输入方案，未打开收藏窗口；请先在 Rime 中确认方案后重试。",
            )
            return
        self._ctx.rime_preview_service.warm_up_async()
        # 抓取到的选中文本（可能为空）
        captured = (text or "").strip()
        notice = ""
        if not captured:
            notice = "未捕获到选中文本。可在这里手动输入，或先复制文本后再收藏。"
        logger.info("热键收藏：打开弹窗 result=%s", "成功" if captured else "空")
        # 采集窗口可独立于隐藏的主窗口显示，避免热键打断当前工作界面。
        self._phrase_manager.open_quick_add(captured, notice=notice)

    def _show_result_bubble(self, ok: bool, message: str) -> None:
        icon = (QSystemTrayIcon.MessageIcon.Information if ok
                else QSystemTrayIcon.MessageIcon.Warning)
        self._tray.showMessage("RIME 配置小工具", message, icon, 5000)
        if not hasattr(self, "_result_toast"):
            self._result_toast = ToastNotification(self)
        self._result_toast.show_result(ok, message)

    def _on_favorite_completed(self, ok: bool, message: str) -> None:
        self._show_result_bubble(ok, message)

    def _on_edit_completed(self, ok: bool, message: str) -> None:
        self._show_result_bubble(ok, message)

    def _sync_autostart_state(self) -> bool:
        enabled = self._ctx.autostart.enabled
        self._ctx.settings.autostart = enabled
        if hasattr(self, "_tray"):
            self._tray.set_autostart_state(enabled)
        if getattr(self, "_settings_widget", None) is not None:
            self._settings_widget.sync_autostart_state()
        return enabled

    def _toggle_autostart_from_tray(self, checked: bool) -> None:
        ok = self._ctx.autostart.enable() if checked else self._ctx.autostart.disable()
        enabled = self._sync_autostart_state()
        if not ok or enabled != checked:
            reason = self._ctx.autostart.status().reason
            self._show_result_bubble(False, f"开机自启动设置失败：{reason}。")
            return
        message = "已启用开机自启动，登录后将最小化至系统托盘。" if checked else "已关闭开机自启动。"
        self._show_result_bubble(True, message)

    def _toggle_hotkey_from_tray(self) -> None:
        enabled = not self._ctx.settings.hotkey_enabled
        self._ctx.settings.hotkey_enabled = enabled
        if enabled:
            self._register_hotkey()
        else:
            self._ctx.hotkey_manager.unregister()
        self._tray.set_hotkey_state(enabled)
        if self._settings_widget is not None:
            self._settings_widget.refresh()

    def _toggle_auto_deploy_from_tray(self, enabled: bool) -> None:
        self._ctx.settings.auto_deploy = bool(enabled)
        self._tray.set_auto_deploy_state(bool(enabled))
        if self._settings_widget is not None:
            self._settings_widget.refresh()

    def _request_show_main(self) -> None:
        """托盘菜单关闭后再恢复窗口，避免 Windows 前台激活竞争。"""
        QTimer.singleShot(0, self.show_main)

    def _request_open_tab(self, index: int) -> None:
        def restore() -> None:
            self.show_main()
            self._select_tab(index)
        QTimer.singleShot(0, restore)

    def _open_settings_from_tray(self) -> None:
        self._request_open_tab(TAB_SETTINGS)

    def _on_hotkey_toggled(self, enabled: bool) -> None:
        if enabled:
            ok = self._register_hotkey()
            self._tray.set_hotkey_state(ok)
        else:
            self._ctx.hotkey_manager.unregister()
            self._tray.set_hotkey_state(False)

    def _on_hotkey_combo_changed(self, new_combo: str) -> None:
        """事务式应用热键；失败时恢复旧组合并给出明确反馈。"""
        old_combo = self._ctx.settings.hotkey_combo
        if new_combo == old_combo:
            self._settings_widget.show_hotkey_apply_result(
                True, f"热键已应用：{new_combo}", applied_combo=new_combo
            )
            return

        if not self._ctx.settings.hotkey_enabled:
            self._ctx.settings.hotkey_combo = new_combo
            self._settings_widget.show_hotkey_apply_result(
                True,
                f"组合已保存：{new_combo}；启用全局热键后生效。",
                applied_combo=new_combo,
            )
            return

        self._ctx.hotkey_manager.unregister()
        self._ctx.settings.hotkey_combo = new_combo
        if self._register_hotkey():
            self._tray.set_hotkey_state(True)
            self._settings_widget.show_hotkey_apply_result(
                True, f"热键已应用：{new_combo}", applied_combo=new_combo
            )
            return

        self._ctx.settings.hotkey_combo = old_combo
        restored = self._register_hotkey()
        self._tray.set_hotkey_state(restored)
        self._settings_widget.show_hotkey_apply_result(
            False,
            f"应用失败：{new_combo} 可能已被占用；"
            f"已恢复原热键 {old_combo}。",
            applied_combo=old_combo,
        )

    def _run_scheduled_backup(self) -> None:
        """Run at startup and hourly; only copy files when the configured interval is due."""
        settings = self._ctx.settings
        if not bool(getattr(settings, "scheduled_backup_enabled", False)):
            return
        from datetime import datetime, timedelta
        policy = settings.scheme_backup_policy(self._ctx.active_scheme_id)
        try:
            last = datetime.fromisoformat(str(policy.get("last_backup_at", "")))
        except (TypeError, ValueError):
            last = None
        interval = max(1, min(365, int(getattr(settings, "backup_interval_days", 7))))
        if last is not None and datetime.now() - last < timedelta(days=interval):
            return
        resolution = self._current_environment()
        if not resolution.writable or resolution.files is None:
            logger.warning("定期备份已跳过：%s", resolution.reason)
            return
        try:
            self._ctx.backup_service.keep = int(policy.get("backup_count", 10))
            report = self._ctx.backup_service.backup_files_report(
                resolution.files.backup_files()
            )
            if any(path is not None for path in report.saved.values()):
                settings.set_scheme_backup_policy(
                    resolution.schema_id,
                    last_backup_at=datetime.now().isoformat(timespec="seconds"),
                )
        except Exception as exc:
            logger.warning("定期备份失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 自启
    # ------------------------------------------------------------------ #
    def _wire_autostart(self) -> None:
        # Repair a moved shortcut before tray/status widgets can clear intent.
        requested = bool(self._ctx.settings.autostart)
        status = self._ctx.autostart.repair_if_requested(requested)
        self._ctx.settings.autostart = status.enabled
        if requested and not status.enabled:
            logger.warning("开机自启动未启用：%s", status.reason)

    # ------------------------------------------------------------------ #
    # 选项卡切换
    # ------------------------------------------------------------------ #
    def _ensure_dictionary_widget(self) -> DictionaryCenterDialog:
        if self._dictionary_widget is not None:
            return self._dictionary_widget
        widget = self._phrase_manager.create_dictionary_center(
            scope=self._dictionary_scope,
            parent=self._tabs,
            embedded=True,
        )
        self._dictionary_widget = widget
        self._replace_lazy_tab(
            TAB_DICTIONARIES, widget, _TABS[TAB_DICTIONARIES]
        )
        return widget

    def _open_dictionary_page(self, scope: str = "all") -> None:
        if self._phrase_manager.has_unsaved_changes():
            QMessageBox.warning(
                self,
                "存在未保存修改",
                "请先保存或撤销词库工作台中的修改，再打开词典中心。",
            )
            self._select_tab(TAB_LIBRARY)
            return
        if scope != self._dictionary_scope and self._dictionary_widget is not None:
            self._reset_dictionary_widget()
        self._dictionary_scope = scope
        self._select_tab(TAB_DICTIONARIES)

    def _reset_dictionary_widget(self) -> None:
        widget = self._dictionary_widget
        if widget is None:
            return
        widget.dispose()
        placeholder = QWidget(self._tabs)
        self._dictionary_widget = None
        self._dictionary_placeholder = placeholder
        self._replace_lazy_tab(
            TAB_DICTIONARIES, placeholder, _TABS[TAB_DICTIONARIES]
        )
        widget.deleteLater()

    def _ensure_symbols_widget(self) -> SymbolsConfigWidget:
        if self._symbols_widget is not None:
            return self._symbols_widget
        widget = SymbolsConfigWidget(
            self._ctx.symbols_repo, self._ctx.backup_service,
            self._ctx.settings, self._ctx.deploy_service,
            deploy_request=self._phrase_manager.request_auto_deploy,
            write_guard=self._prepare_scheme_write,
        )
        self._symbols_widget = widget
        self._replace_lazy_tab(TAB_SYMBOLS, widget, _TABS[TAB_SYMBOLS])
        return widget

    def _patch_service(self) -> RimePatchService:
        return RimePatchService(
            self._ctx.sandbox_service.active_rime_dir(),
            self._ctx.backup_service,
            self._ctx.file_transaction_writer,
        )

    def _ensure_input_widget(self) -> InputHabitsWidget:
        if self._input_widget is None:
            self._input_widget = InputHabitsWidget(
                self._patch_service,
                self._current_environment,
                self._phrase_manager.request_auto_deploy,
                self._tabs,
            )
            self._replace_lazy_tab(TAB_INPUT, self._input_widget, _TABS[TAB_INPUT])
            self._input_widget.activate()
        return self._input_widget

    def _ensure_settings_widget(self) -> SettingsWidget:
        if self._settings_widget is not None:
            return self._settings_widget
        widget = SettingsWidget(
            self._ctx.settings, self._ctx.autostart,
            self._ctx.deploy_service, self._ctx.schema_repo,
            backup=self._ctx.backup_service,
            health_opener=self._phrase_manager.open_health_check,
            dictionary_center_opener=self._open_dictionary_page,
            data_engine_client=self._ctx.data_engine_client,
            environment_provider=self._current_environment,
            deploy_request=self._phrase_manager.request_manual_deploy,
            wanxiang_update_service_provider=lambda: WanxiangUpdateService(
                self._ctx.sandbox_service.active_rime_dir(),
                self._ctx.backup_service,
                self._ctx.file_transaction_writer,
            ),
            background_tasks_enabled=self._background_tasks_enabled,
        )
        widget.rimeDirChanged.connect(self._on_rime_dir_changed)
        widget.hotkeyToggled.connect(self._on_hotkey_toggled)
        widget.hotkeyComboChanged.connect(self._on_hotkey_combo_changed)
        widget.sandboxToggled.connect(self._on_sandbox_toggled)
        widget.themeChanged.connect(self._on_theme_changed)
        self._phrase_manager.manualDeploymentCompleted.connect(
            widget.handle_wanxiang_deploy_result
        )
        self._settings_widget = widget
        self._settings_widget.sync_autostart_state()
        self._replace_lazy_tab(TAB_SETTINGS, widget, _TABS[TAB_SETTINGS])
        return widget

    def _replace_lazy_tab(self, index: int, widget: QWidget, title: str) -> None:
        current = self._tabs.currentIndex()
        self._tabs.blockSignals(True)
        try:
            self._tabs.removeTab(index)
            self._tabs.insertTab(index, widget, title)
            self._tabs.setCurrentIndex(current)
        finally:
            self._tabs.blockSignals(False)

    def _on_tab_changed(self, index: int) -> None:
        self._tab_activation_generation += 1
        generation = self._tab_activation_generation
        if index == TAB_LIBRARY:
            # Environment changes are already handled by RimeEnvironmentMonitor.
            # Avoid synchronous YAML resolution on every return to the workbench.
            return
        if index == TAB_DICTIONARIES:
            if self._phrase_manager.has_unsaved_changes():
                QMessageBox.warning(
                    self,
                    "存在未保存修改",
                    "请先保存或撤销词库工作台中的修改，再打开词典中心。",
                )
                QTimer.singleShot(0, lambda: self._select_tab(TAB_LIBRARY))
                return
        # Let the new stack page paint before constructing a potentially large
        # lazy page. Rapid switching invalidates stale intermediate activations.
        QTimer.singleShot(0, lambda: self._activate_tab(index, generation))

    def _activate_tab(self, index: int, generation: int) -> None:
        if generation != self._tab_activation_generation:
            return
        if self._tabs.currentIndex() != index:
            return
        if index == TAB_DICTIONARIES:
            self._ensure_dictionary_widget()
        elif index == TAB_INPUT:
            self._ensure_input_widget()
        elif index == TAB_SYMBOLS:
            self._ensure_symbols_widget().refresh_categories()
        elif index == TAB_SETTINGS:
            # Settings changes refresh themselves.  Re-entering the tab must not
            # rescan every configured dictionary on the GUI thread.
            self._ensure_settings_widget()

    def _select_tab(self, index: int, refresh: bool = True) -> None:
        """切换选项卡；热键弹窗路径可跳过切页刷新。"""
        if refresh:
            self._tabs.setCurrentIndex(index)
            return
        self._tabs.blockSignals(True)
        try:
            self._tabs.setCurrentIndex(index)
        finally:
            self._tabs.blockSignals(False)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _start_optional_services(self) -> None:
        if self._disposed or not self._background_tasks_enabled:
            return
        if self._optional_services_started:
            return
        self._optional_services_started = True
        self._ctx.system_dictionary_index.ensure_ready_async()
        self._index_status_timer.start()
        self._ctx.rime_preview_service.warm_up_async()

    def show_main(self) -> None:
        self._start_optional_services()
        # An autostart instance has never created a native window. Explicitly
        # show it before restoring normal state so Windows can foreground it.
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        phrase_manager = getattr(self, "_phrase_manager", None)
        if phrase_manager is not None:
            phrase_manager.refresh_scheme_status()

    def deploy_now(self) -> None:
        """Queue a manual deployment without blocking the Qt event loop."""
        if self._phrase_manager.request_manual_deploy():
            self._tray.action_deploy.setEnabled(False)
            self._tray.action_deploy.setText("正在重新部署…")

    def _on_manual_deployment_completed(self, ok: bool, message: str) -> None:
        service = getattr(self, "_wanxiang_background_pending_service", None)
        if service is not None:
            self._wanxiang_background_pending_service = None
            if ok:
                _confirmed, backup_message = service.confirm_update()
                text = "万象更新已部署并通过小狼毫验证。"
                if backup_message:
                    text = f"{text} {backup_message}"
                self._ctx.settings.set("wanxiang_update_state", text)
            else:
                rollback_ok, rollback_message = service.rollback_last_update()
                self._ctx.settings.set(
                    "wanxiang_update_state",
                    f"万象更新部署失败：{message}；{rollback_message}",
                )
                self._ctx.rebuild_repos()
                self._reattach_repos()
                if rollback_ok:
                    self._phrase_manager.request_manual_deploy()
        self._update_tray_deploy()
        icon = (QSystemTrayIcon.MessageIcon.Information if ok
                else QSystemTrayIcon.MessageIcon.Warning)
        self._tray.showMessage("重新部署", message, icon, 6000)

    def _update_tray_deploy(self) -> None:
        """同步托盘手动部署动作的可用状态与文案。"""
        if not hasattr(self, "_tray"):
            return
        sandbox = self._ctx.settings.sandbox_mode
        available = self._ctx.deploy_service.available and not sandbox
        if sandbox:
            self._tray.action_deploy.setText("立即重新部署（沙盒禁用）")
        else:
            self._tray.action_deploy.setText("立即重新部署")
        self._tray.action_deploy.setEnabled(available)
        self._tray.set_auto_deploy_state(self._ctx.settings.auto_deploy)

    def _on_theme_changed(self, theme: str) -> None:
        """主题切换：持久化 + 即时应用（仅重设样式，不重建业务对象/不丢表单）。"""
        self._ctx.settings.theme = theme
        apply_theme(theme)
        apply_window_theme(self, theme)
        # 分组侧栏『全部』按钮主色是显式样式表（取当前 @ACCENT@），
        # QApplication.setStyleSheet 不会自动重算该控件，故主题切换后主动 restyle。
        # 必须在 apply_theme 之后调用，使 accent_color() 已是新主题值。
        self._phrase_manager.refresh_visuals()

    def _reattach_repos(self) -> None:
        """Rime 目录 / 沙盒模式变化后，将页面数据源指向重建后的仓储。"""
        self._phrase_manager.reattach(
            self._ctx.phrase_repo,
            self._ctx.group_service,
            self._ctx.backup_service,
            self._ctx.system_dictionary_index,
            self._ctx.rime_preview_service, self._ctx.metadata_store,
            self._ctx.schema_repo.speller_alphabet(),
        )
        self._reset_dictionary_widget()
        if self._input_widget is not None:
            self._input_widget.activate()
        if self._symbols_widget is not None:
            self._symbols_widget.reattach(
                self._ctx.symbols_repo, self._ctx.backup_service,
            )
        if self._settings_widget is not None:
            self._settings_widget.set_schema_repo(self._ctx.schema_repo)

    def _on_rime_dir_changed(self, new_dir: str) -> None:
        if not self._phrase_manager.confirm_discard_changes("切换 Rime 目录"):
            self._ctx.settings.rime_dir = self._attached_rime_dir
            if self._settings_widget is not None:
                self._settings_widget.refresh()
            return
        self._ctx.rebuild_repos()
        self._attached_scheme_id = self._ctx.active_scheme_id
        self._attached_rime_dir = new_dir
        self._reattach_repos()
        self._check_rime_dir()
        self._wire_hotkey()
        self._update_tray_deploy()
        self._select_tab(TAB_LIBRARY)

    def _on_sandbox_toggled(self, enabled: bool) -> None:
        if not self._phrase_manager.confirm_discard_changes("切换沙盒模式"):
            self._ctx.settings.sandbox_mode = self._attached_sandbox_mode
            if self._settings_widget is not None:
                self._settings_widget.refresh()
            return
        # 沙盒开关变化：重建仓储（指向副本或真实目录），保持当前页便于看见设置状态。
        current_index = self._tabs.currentIndex()
        self._ctx.rebuild_repos()
        self._attached_scheme_id = self._ctx.active_scheme_id
        self._attached_sandbox_mode = enabled
        self._reattach_repos()
        self._check_rime_dir()
        self._wire_hotkey()
        if self._settings_widget is not None:
            self._settings_widget.refresh()
        self._update_tray_deploy()
        if self._ctx.settings.is_rime_dir_valid():
            self._select_tab(current_index)

    def _check_rime_dir(self) -> None:
        if self._ctx.settings.is_rime_dir_valid():
            version = self._ctx.deploy_service.weasel_version
            version_text = f" v{version}" if version else ""
            scheme = self._ctx.active_scheme.files.display_name if self._ctx.active_scheme.files else "方案未确认"
            if self._ctx.settings.sandbox_mode:
                self._tabs.set_connection_status(
                    f"●  已连接 Rime{version_text} · {scheme} · 沙盒", "warning")
            else:
                state = "ready" if self._ctx.active_scheme.writable else "warning"
                self._tabs.set_connection_status(
                    f"●  已连接 Rime{version_text} · {scheme}", state)
            return
        self._tabs.set_connection_status("●  未连接 Rime", "error")
        if not self._ctx.settings.is_rime_dir_valid():
            self._select_tab(TAB_SETTINGS)
            QMessageBox.warning(
                self, "未设置 Rime 目录",
                "未检测到有效的 Rime 用户目录，请到「设置」页手动指定。",
            )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.ActivationChange
                and self.isActiveWindow()
                and hasattr(self, "_phrase_manager")):
            QTimer.singleShot(0, self._environment_monitor.check_now)

    def _on_environment_changed(self, resolution) -> None:
        """Reattach every scheme-scoped page only after a real state change."""
        if not hasattr(self, "_phrase_manager"):
            return
        if resolution.schema_id == self._attached_scheme_id \
                and resolution.writable == self._ctx.active_scheme.writable:
            self._check_rime_dir()
            return
        if self._phrase_manager.has_unsaved_changes():
            self._phrase_manager.show_environment_warning(
                "检测到 Rime 方案变化；当前有未保存修改，已暂停切换。请先保存或撤销。"
            )
            return
        self._ctx.rebuild_repos()
        self._attached_scheme_id = self._ctx.active_scheme_id
        self._reattach_repos()
        self._index_status_timer.start()
        self._check_rime_dir()

    def _current_environment(self):
        """Refresh the tiny Rime state files before a page loads or saves."""
        if not hasattr(self, "_environment_monitor"):
            return self._ctx.active_scheme
        resolution = self._environment_monitor.check_now(force=True)
        if resolution.schema_id != self._attached_scheme_id:
            self._on_environment_changed(resolution)
        return resolution

    def _prepare_scheme_write(self) -> bool:
        resolution = self._current_environment()
        current = self._ctx.active_scheme
        return bool(
            resolution.writable and current.writable
            and resolution.schema_id == self._attached_scheme_id
            and current.schema_id == self._attached_scheme_id
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "_environment_monitor"):
            self._environment_monitor.set_window_visible(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if hasattr(self, "_environment_monitor"):
            self._environment_monitor.set_window_visible(False)
    def closeEvent(self, event) -> None:
        if self._disposed:
            event.accept()
            return
        # 关闭窗口 → 最小化到托盘，不退出
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "RIME 配置小工具",
            "已最小化到系统托盘，右键托盘可退出。",
            QSystemTrayIcon.MessageIcon.Information,
        )

    def quit_app(self) -> None:
        if not self._phrase_manager.confirm_discard_changes("退出程序"):
            self.show_main()
            self._select_tab(TAB_LIBRARY)
            return
        self.dispose()
        QApplication.quit()

    def dispose(self) -> None:
        """Stop owned background work and destroy a window instead of hiding it."""
        if self._disposed:
            return
        self._disposed = True
        self._tab_activation_generation += 1
        for timer in (
            self._scheduled_backup_timer,
            self._wanxiang_background_poll,
            self._index_status_timer,
        ):
            timer.stop()
        self._environment_monitor.stop()
        if self._dictionary_widget is not None:
            self._dictionary_widget.dispose()
        if self._settings_widget is not None:
            self._settings_widget.dispose()
        self._phrase_manager.dispose()
        self._tray.hide()
        self._ctx.shutdown()
        self.hide()
        self.deleteLater()
