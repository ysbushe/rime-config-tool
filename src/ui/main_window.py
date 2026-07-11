"""主窗口（MainWindow）。

装配三个选项卡（顶部横向导航 + 堆叠页）：
    1. 词库管理  2. 符号表  3. 设置
并接线：系统托盘（TrayIcon）、全局热键（HotkeyManager）、开机自启（Autostart）。
加载风格 A 的 application.qss。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src import __version__

from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSystemTrayIcon,
    QTabWidget,
    QWidget,
)

from src.app_context import AppContext
from src.ui.phrase_manager import PhraseManager
from src.ui.settings_widget import SettingsWidget
from src.ui.symbols_config_widget import SymbolsConfigWidget
from src.ui.theme import apply_theme, apply_window_theme, set_ink_decor
from src.ui.tray_icon import TrayIcon
from src.utils.logger import get_logger

logger = get_logger(__name__)

_QSS_PATH = Path(__file__).resolve().parent / "application.qss"

_TABS = ["词库管理", "符号表", "设置"]


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, context: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._ctx = context
        self.setWindowTitle(f"RIME 配置小工具 v{__version__}")
        self.resize(980, 640)
        self._build_ink_decor()
        self._apply_style()
        self._set_app_icon()

        self._build_pages()
        self._build_tray()
        self._wire_hotkey()
        self._wire_autostart()

        # 应用当前主题（作用于 QApplication，弹窗自动继承）
        self._update_tray_deploy()

        self._select_tab(0)
        self._check_rime_dir()

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
    # 应用图标（程序化生成，托盘与主窗口共用，不依赖外部文件）
    # ------------------------------------------------------------------ #
    def _set_app_icon(self) -> None:
        icon = self._make_default_icon()
        # 若 assets/app.ico 存在则优先使用
        try:
            from PySide6.QtGui import QIcon

            icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "app.ico"
            if icon_path.exists():
                icon = QIcon(str(icon_path))
        except Exception:
            pass
        self.setWindowIcon(icon)
        self._app_icon = icon

    def _make_default_icon(self) -> "QIcon":
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

        size = 64
        pm = QPixmap(size, size)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#185FA5"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(6, 6, size - 12, size - 12, 14, 14)
        p.setPen(QColor("#FFFFFF"))
        p.setFont(QFont("Microsoft YaHei UI", 34, QFont.Weight.Bold))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "R")
        p.end()
        return QIcon(pm)

    # ------------------------------------------------------------------ #
    # 顶部选项卡 + 堆叠页
    # ------------------------------------------------------------------ #
    def _build_pages(self) -> None:
        ctx = self._ctx
        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self._tabs.setObjectName("TopTabs")
        # 顶部选项卡按窗口宽度平均分配
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)

        self._phrase_manager = PhraseManager(
            ctx.phrase_repo, ctx.group_service, ctx.backup_service,
            ctx.settings, ctx.deploy_service, ctx.pinyin_service,
        )
        self._symbols_widget = SymbolsConfigWidget(
            ctx.symbols_repo, ctx.backup_service, ctx.settings, ctx.deploy_service,
        )
        self._settings_widget = SettingsWidget(
            ctx.settings, ctx.autostart, ctx.deploy_service, ctx.schema_repo,
            backup=ctx.backup_service,
        )
        self._settings_widget.rimeDirChanged.connect(self._on_rime_dir_changed)
        self._settings_widget.hotkeyToggled.connect(self._on_hotkey_toggled)
        self._settings_widget.hotkeyComboChanged.connect(self._on_hotkey_combo_changed)
        self._settings_widget.sandboxToggled.connect(self._on_sandbox_toggled)
        self._settings_widget.themeChanged.connect(self._on_theme_changed)

        self._tabs.addTab(self._phrase_manager, _TABS[0])
        self._tabs.addTab(self._symbols_widget, _TABS[1])
        self._tabs.addTab(self._settings_widget, _TABS[2])
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self.setCentralWidget(self._tabs)

    # ------------------------------------------------------------------ #
    # 托盘
    # ------------------------------------------------------------------ #
    def _build_tray(self) -> None:
        self._tray = TrayIcon(self)
        # 使用主窗口初始化的应用图标（程序化生成，托盘必有图标）
        self._tray.set_icon(getattr(self, "_app_icon", QApplication.windowIcon()))
        self._tray.action_open.triggered.connect(self.show_main)
        self._tray.requestOpen.connect(self.show_main)
        self._tray.action_deploy.triggered.connect(self.deploy_now)
        self._tray.action_hotkey.triggered.connect(self._toggle_hotkey_from_tray)
        self._tray.action_settings.triggered.connect(lambda: self._select_tab(2))
        self._tray.action_quit.triggered.connect(self.quit_app)
        self._tray.set_hotkey_state(self._ctx.settings.hotkey_enabled)
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
        # 抓取到的选中文本（可能为空）
        captured = (text or "").strip()
        notice = ""
        if not captured:
            notice = "未捕获到选中文本。可在这里手动输入，或先复制文本后再收藏。"
        logger.info("热键收藏：打开弹窗 result=%s", "成功" if captured else "空")
        # 切到词库页并弹快速收藏
        self._select_tab(0, refresh=False)
        self.show_main()
        self._phrase_manager.open_quick_add(captured, notice=notice)

    def _toggle_hotkey_from_tray(self) -> None:
        enabled = not self._ctx.settings.hotkey_enabled
        self._ctx.settings.hotkey_enabled = enabled
        if enabled:
            self._register_hotkey()
        else:
            self._ctx.hotkey_manager.unregister()
        self._tray.set_hotkey_state(enabled)
        self._settings_widget.refresh()

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

    # ------------------------------------------------------------------ #
    # 自启
    # ------------------------------------------------------------------ #
    def _wire_autostart(self) -> None:
        # 启动时按设置同步：若设置开启但快捷方式缺失则补建
        if self._ctx.settings.autostart and not self._ctx.autostart.enabled:
            self._ctx.autostart.enable()

    # ------------------------------------------------------------------ #
    # 选项卡切换
    # ------------------------------------------------------------------ #
    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            self._phrase_manager.refresh()
        elif index == 1:
            self._symbols_widget.refresh_categories()
        elif index == 2:
            self._settings_widget.refresh()

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
    def show_main(self) -> None:
        self.showNormal()
        self.activateWindow()

    def deploy_now(self) -> None:
        ok, msg = self._ctx.deploy_service.deploy()
        if ok:
            QMessageBox.information(self, "部署", msg)
        else:
            QMessageBox.warning(self, "部署", msg)

    def _update_tray_deploy(self) -> None:
        """同步托盘『一键部署』的可用状态与文案（沙盒/部署器不可用则禁用）。"""
        if not hasattr(self, "_tray"):
            return
        sandbox = self._ctx.settings.sandbox_mode
        available = self._ctx.deploy_service.available and not sandbox
        if sandbox:
            self._tray.action_deploy.setText("一键部署（沙盒禁用）")
        else:
            self._tray.action_deploy.setText("一键部署")
        self._tray.action_deploy.setEnabled(available)

    def _on_theme_changed(self, theme: str) -> None:
        """主题切换：持久化 + 即时应用（仅重设样式，不重建业务对象/不丢表单）。"""
        self._ctx.settings.theme = theme
        apply_theme(theme)
        apply_window_theme(self, theme)
        # 分组侧栏『全部』按钮主色是显式样式表（取当前 @ACCENT@），
        # QApplication.setStyleSheet 不会自动重算该控件，故主题切换后主动 restyle。
        # 必须在 apply_theme 之后调用，使 accent_color() 已是新主题值。
        self._phrase_manager.restyle()
        # 冲突行高亮背景随主题变化，触发表格重绘（仅视觉，不改动数据）
        try:
            self._phrase_manager._table.model().layoutChanged.emit()
        except Exception:
            pass

    def _reattach_repos(self) -> None:
        """Rime 目录 / 沙盒模式变化后，将页面数据源指向重建后的仓储。"""
        self._phrase_manager.reattach(
            self._ctx.phrase_repo,
            self._ctx.group_service,
            self._ctx.backup_service,
        )
        self._symbols_widget._repo = self._ctx.symbols_repo
        self._settings_widget.set_schema_repo(self._ctx.schema_repo)

    def _on_rime_dir_changed(self, new_dir: str) -> None:
        self._ctx.rebuild_repos()
        self._reattach_repos()
        self._check_rime_dir()
        self._wire_hotkey()
        self._update_tray_deploy()
        self._select_tab(0)

    def _on_sandbox_toggled(self, _enabled: bool) -> None:
        # 沙盒开关变化：重建仓储（指向副本或真实目录），保持当前页便于看见设置状态。
        current_index = self._tabs.currentIndex()
        self._ctx.rebuild_repos()
        self._reattach_repos()
        self._check_rime_dir()
        self._wire_hotkey()
        self._settings_widget.refresh()
        self._update_tray_deploy()
        if self._ctx.settings.is_rime_dir_valid():
            self._select_tab(current_index)
    def _check_rime_dir(self) -> None:
        if not self._ctx.settings.is_rime_dir_valid():
            self._select_tab(2)
            QMessageBox.warning(
                self, "未设置 Rime 目录",
                "未检测到有效的 Rime 用户目录，请到「设置」页手动指定。",
            )

    def closeEvent(self, event) -> None:
        # 关闭窗口 → 最小化到托盘，不退出
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "RIME 配置小工具",
            "已最小化到系统托盘，右键托盘可退出。",
            QSystemTrayIcon.MessageIcon.Information,
        )

    def quit_app(self) -> None:
        self._ctx.hotkey_manager.unregister()
        QApplication.quit()
