"""UI 优化专项测试（基于临时副本，不碰真实 Rime 配置）。

覆盖：
    - 符号删除：取消不删除、确认才删除（且仅内存，需保存才写盘）
    - 主题：即时切换、持久化、两套主题的复选框资源与样式
    - 沙盒：设置页与托盘『一键部署』禁用状态
全部使用 temp_rime_dir 副本，测试不触及真实 Rime 目录。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QInputDialog

from src.repo.symbols_repo import SymbolsRepo
from src.service.backup_service import BackupService
from src.ui.symbols_config_widget import SymbolsConfigWidget
from src.ui.theme import (
    apply_theme,
    current_theme,
    load_theme_qss,
    qss_path,
)


class _FakeSettings:
    def __init__(self, sandbox_mode: bool = False) -> None:
        self.rime_dir = ""
        self.deployer_path = ""
        self.backup_count = 5
        self.hotkey_combo = "Ctrl+Alt+Q"
        self.auto_deploy = False
        self.hotkey_enabled = True
        self.autostart = False
        self.theme = "light"
        self.sandbox_mode = sandbox_mode

    def set(self, key: str, value) -> None:
        setattr(self, key, value)


class _FakeAutostart:
    enabled = False

    def enable(self) -> bool:
        self.enabled = True
        return True

    def disable(self) -> bool:
        self.enabled = False
        return True


class _FakeDeploy:
    available = True
    deployer_path = "C:/Rime/WeaselDeployer.exe"

    def redetect(self) -> None:
        pass

    def deploy(self):
        return True, "ok"


def _build_symbols_widget(temp_rime_dir, sandbox_mode: bool = False):
    repo = SymbolsRepo(str(temp_rime_dir / "symbols_v.yaml"))
    backup = BackupService(str(temp_rime_dir), keep=5)
    settings = _FakeSettings(sandbox_mode=sandbox_mode)
    deploy = _FakeDeploy()
    widget = SymbolsConfigWidget(repo, backup, settings, deploy)
    return widget, repo


# --------------------------------------------------------------------------- #
# 符号删除确认（使用 fixtures 中已存在的符号 ™，避免内存/磁盘混淆）
# --------------------------------------------------------------------------- #
def test_symbol_delete_cancel_keeps_symbol(qapp, temp_rime_dir, monkeypatch) -> None:
    widget, repo = _build_symbols_widget(temp_rime_dir)
    widget._current_category = "/fh"

    monkeypatch.setattr(
        "src.ui.symbols_config_widget.confirm_delete",
        lambda *a, **k: False,
    )
    widget._on_del_symbol("™")

    # 取消：列表与未保存状态均不变
    assert "™" in repo.get_symbols("/fh")
    assert widget._dirty is False


def test_symbol_delete_confirm_removes_only_in_memory(qapp, temp_rime_dir, monkeypatch) -> None:
    widget, repo = _build_symbols_widget(temp_rime_dir)
    widget._current_category = "/fh"
    assert "™" in repo.get_symbols("/fh")

    monkeypatch.setattr(
        "src.ui.symbols_config_widget.confirm_delete",
        lambda *a, **k: True,
    )
    widget._on_del_symbol("™")

    # 确认：内存移除并标记未保存
    assert "™" not in repo.get_symbols("/fh")
    assert widget._dirty is True

    # 但仍未写盘：从磁盘重新载入，符号仍在
    reloaded = SymbolsRepo(str(temp_rime_dir / "symbols_v.yaml"))
    assert "™" in reloaded.get_symbols("/fh")


# --------------------------------------------------------------------------- #
# 主题切换与持久化
# --------------------------------------------------------------------------- #
def test_theme_switch_and_persist(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "settings.json"
    monkeypatch.setattr("src.settings.settings_path", lambda: fake)
    from src.settings import Settings

    s = Settings()
    assert s.theme == "light"
    s.theme = "dark"
    assert s.theme == "dark"
    # 重新载入应从磁盘恢复
    s2 = Settings()
    assert s2.theme == "dark"


def test_apply_theme_sets_current_and_stylesheet(qapp) -> None:
    apply_theme("dark")
    assert current_theme() == "dark"
    assert ":/rimeconfig/check-dark.svg" in qapp.styleSheet()
    assert "file://" not in qapp.styleSheet()
    apply_theme("light")
    assert current_theme() == "light"


# --------------------------------------------------------------------------- #
# 两套主题的复选框资源与样式
# --------------------------------------------------------------------------- #
def test_both_themes_load_and_reference_check_svg() -> None:
    for theme in ("light", "dark"):
        qss = load_theme_qss(theme)
        # 内置资源地址不受发布目录变化影响。
        assert "url(check.svg)" not in qss
        assert f":/rimeconfig/check-{theme}.svg" in qss
        assert ":/rimeconfig/check-disabled.svg" in qss
        assert "file://" not in qss
        assert Path(qss_path(theme)).exists()

    light = load_theme_qss("light")
    dark = load_theme_qss("dark")
    for qss in (light, dark):
        # 选中态显示勾选符号（image 引用 check.svg）
        assert "QCheckBox::indicator:checked" in qss
        assert "QTableView::indicator:checked" in qss
        assert "image: url(" in qss
        # 五态：未选 / 选中 / 半选 / 禁用选中 / 禁用未选
        assert "indicator:unchecked" in qss
        assert "indicator:indeterminate" in qss
        assert "indicator:disabled:checked" in qss
        assert "indicator:disabled:unchecked" in qss


# --------------------------------------------------------------------------- #
# 沙盒下设置页与托盘部署禁用
# --------------------------------------------------------------------------- #
def test_sandbox_disables_settings_deploy(qapp, temp_rime_dir) -> None:
    from src.ui.settings_widget import SettingsWidget

    widget = SettingsWidget(_FakeSettings(sandbox_mode=True), _FakeAutostart(), _FakeDeploy())
    assert widget._btn_deploy.isEnabled() is False
    assert "沙盒" in widget._lbl_deploy.text()


def test_sandbox_disables_tray_deploy(qapp, app_context) -> None:
    from src.ui.main_window import MainWindow

    # 避免首次自动分组弹窗干扰
    app_context.settings.auto_group_done = True
    mw = MainWindow(app_context)

    app_context.settings.sandbox_mode = True
    mw._update_tray_deploy()
    assert mw._tray.action_deploy.isEnabled() is False
    assert "沙盒" in mw._tray.action_deploy.text()

    # 退出沙盒后仍可由可用性决定
    app_context.settings.sandbox_mode = False
    mw._update_tray_deploy()
    # 部署器在测试环境可能不可用，但沙盒标签必须移除
    assert "沙盒" not in mw._tray.action_deploy.text()


def test_result_toast_separates_phrase_and_codes(qapp) -> None:
    from src.ui.toast_notification import ToastNotification

    toast = ToastNotification()
    toast.show_result(True, "已收藏「银行」：yin'hang、yh。")

    assert toast._detail.isVisible()
    assert toast._detail_text.text() == "「银行」"
    assert "yin'hang" in toast._detail_codes.text()
    assert toast._detail_codes.wordWrap() is True


def test_release_ico_is_loadable() -> None:
    """目录式发布包和系统托盘共用的 ICO 必须能被 Qt 读取。"""
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    icon = QIcon(str(icon_path))
    assert icon_path.is_file()
    assert not icon.isNull()
    assert not icon.pixmap(16, 16).isNull()
