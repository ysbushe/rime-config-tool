"""UI 优化专项测试（基于临时副本，不碰真实 Rime 配置）。

覆盖：
    - 符号删除：取消不删除、确认才删除（且仅内存，需保存才写盘）
    - 主题：即时切换、持久化、两套主题的复选框资源与样式
    - 沙盒：设置页与托盘『一键部署』禁用状态
全部使用 temp_rime_dir 副本，测试不触及真实 Rime 目录。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QInputDialog, QLabel

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


def _wanxiang_update_report():
    return SimpleNamespace(
        current_version="17.2.5",
        new_version="17.2.6",
        tool_version="test",
        reason="已通过结构预检",
        compatible=True,
        download_url="https://example.test/wanxiang.zip",
        requires_confirmation=False,
        waiting_for_adapter=False,
    )


def test_wanxiang_confirmation_explains_time_and_defaults_to_cancel(qapp) -> None:
    from src.ui.wanxiang_update_notice import WanxiangUpdateConfirmationDialog

    dialog = WanxiangUpdateConfirmationDialog(_wanxiang_update_report())
    text = "\n".join(label.text() for label in dialog.findChildren(QLabel))

    assert "17.2.5" in text and "17.2.6" in text
    assert "1～2 分钟" in text
    assert "下载并校验" in text
    assert "中文输入" in text
    warning_title = dialog.findChild(QLabel, "WanxiangInputWarningTitle")
    warning_detail = dialog.findChild(QLabel, "WanxiangInputWarningDetail")
    assert warning_title is not None and "暂时不可用" in warning_title.text()
    assert warning_detail is not None
    assert "部分待输入窗口可能短暂无响应" in warning_detail.text()
    assert "强烈建议" in warning_detail.text()
    assert "暂无中文输入需求时再升级" in warning_detail.text()
    qss = load_theme_qss("light")
    assert "QFrame#WanxiangInputWarning" in qss
    assert "QLabel#WanxiangInputWarningTitle" in qss
    assert "font-size: 17px" in qss
    assert dialog.cancel_button.isDefault()


def test_wanxiang_test_entry_is_visible_only_in_gui_test_mode(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    monkeypatch.delenv("RIME_CONFIG_GUI_TEST", raising=False)
    normal = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        background_tasks_enabled=False,
    )
    assert normal._btn_wanxiang_test.isHidden()
    assert normal._wanxiang_test_tools.isHidden()
    normal.dispose()

    monkeypatch.setenv("RIME_CONFIG_GUI_TEST", "1")
    testing = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        background_tasks_enabled=False,
    )
    assert not testing._btn_wanxiang_test.isHidden()
    assert not testing._wanxiang_test_tools.isHidden()
    testing.dispose()


def test_wanxiang_test_entry_never_calls_update_or_deploy(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []
    service = SimpleNamespace(
        current_version=lambda: "17.2.5",
        update=lambda *_args, **_kwargs: calls.append("update"),
    )
    monkeypatch.setenv("RIME_CONFIG_GUI_TEST", "1")
    monkeypatch.setattr(
        "src.ui.settings_widget.WanxiangUpdateConfirmationDialog.exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        deploy_request=lambda: calls.append("deploy"),
        wanxiang_update_service_provider=lambda: service,
        background_tasks_enabled=False,
    )

    widget._show_wanxiang_update_test_confirmation()

    assert calls == []
    assert "未下载" in widget._lbl_wanxiang_update.text()


def test_wanxiang_deployment_simulation_is_modal_and_has_no_side_effects(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []
    monkeypatch.setenv("RIME_CONFIG_GUI_TEST", "1")
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        deploy_request=lambda: calls.append("deploy"),
        background_tasks_enabled=False,
    )

    widget._show_wanxiang_deployment_test()
    progress = widget._wanxiang_test_progress

    assert progress is not None and progress.active
    assert progress.windowModality() == Qt.WindowModality.ApplicationModal
    assert widget._wanxiang_test_timer.isActive()
    assert calls == []

    widget._wanxiang_test_timer.stop()
    widget._finish_wanxiang_deployment_test()
    assert widget._wanxiang_test_progress is None
    assert "应用已恢复" in widget._lbl_wanxiang_update.text()


def test_quick_capture_simulation_discards_accepted_values(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []

    class FakeQuickAddDialog:
        def __init__(self, **kwargs):
            assert kwargs["prefill_text"] == "银行"

        def exec(self):
            return QDialog.DialogCode.Accepted

        def get_values(self):
            return {
                "display_code": "",
                "additional_codes": ["y'h"],
            }

        def dispose(self):
            calls.append("disposed")

        def deleteLater(self):
            calls.append("deleted")

    monkeypatch.setenv("RIME_CONFIG_GUI_TEST", "1")
    monkeypatch.setattr(
        "src.ui.quick_add_dialog.QuickAddDialog", FakeQuickAddDialog,
    )
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        deploy_request=lambda: calls.append("deploy"),
        background_tasks_enabled=False,
    )

    widget._show_quick_capture_test()

    assert calls == ["disposed", "deleted"]
    assert "默认编码已删除" in widget._lbl_wanxiang_update.text()
    assert "y'h" in widget._lbl_wanxiang_update.text()
    assert "未写入个人词库" in widget._lbl_wanxiang_update.text()


def test_settings_cancelled_wanxiang_confirmation_never_calls_update(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []
    service = SimpleNamespace(update=lambda *_args, **_kwargs: calls.append(True))
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        wanxiang_update_service_provider=lambda: service,
        background_tasks_enabled=False,
    )
    widget._wanxiang_report = _wanxiang_update_report()
    monkeypatch.setattr(
        "src.ui.settings_widget.WanxiangUpdateConfirmationDialog.exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )

    widget._start_wanxiang_update()

    assert calls == []


def test_confirmed_force_update_starts_only_after_acceptance(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []
    service = SimpleNamespace(
        update=lambda _report, *, force=False: calls.append(force) or (True, "ok"),
    )
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
        wanxiang_update_service_provider=lambda: service,
        background_tasks_enabled=False,
    )
    widget._wanxiang_report = _wanxiang_update_report()
    monkeypatch.setattr(
        "src.ui.settings_widget.WanxiangUpdateConfirmationDialog.exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr("src.ui.settings_widget.threading.Thread", ImmediateThread)

    widget._start_wanxiang_update(force=True)

    assert calls == [True]


def test_background_auto_update_cancel_never_calls_update(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.main_window import MainWindow

    calls = []
    service = SimpleNamespace(update=lambda *_args, **_kwargs: calls.append(True))
    app_context.settings.set("wanxiang_auto_update", True)
    window = MainWindow(app_context, background_tasks_enabled=False)
    window._wanxiang_background_results.put(
        ("check", service, _wanxiang_update_report(), None, None)
    )
    monkeypatch.setattr(
        "src.ui.main_window.WanxiangUpdateConfirmationDialog.exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )

    window._drain_wanxiang_background_result()

    assert calls == []
    window.dispose()


def test_sandbox_disables_tray_deploy(qapp, app_context) -> None:
    from src.ui.main_window import MainWindow

    # 避免首次自动分组弹窗干扰
    app_context.settings.auto_group_done = True
    mw = MainWindow(app_context, background_tasks_enabled=False)

    app_context.settings.sandbox_mode = True
    mw._update_tray_deploy()
    assert mw._tray.action_deploy.isEnabled() is False
    assert "沙盒" in mw._tray.action_deploy.text()

    # 退出沙盒后仍可由可用性决定
    app_context.settings.sandbox_mode = False
    mw._update_tray_deploy()
    # 部署器在测试环境可能不可用，但沙盒标签必须移除
    assert "沙盒" not in mw._tray.action_deploy.text()
    mw.dispose()
    qapp.processEvents()


def test_tray_manual_deploy_is_queued_without_blocking_ui(
        qapp, app_context, monkeypatch) -> None:
    from src.ui.main_window import MainWindow

    app_context.settings.auto_group_done = True
    window = MainWindow(app_context, background_tasks_enabled=False)
    requests = []
    notices = []
    monkeypatch.setattr(
        window._phrase_manager, "request_manual_deploy",
        lambda: requests.append(True) or True,
    )
    monkeypatch.setattr(
        window._tray, "showMessage",
        lambda *args: notices.append(args),
    )

    window.deploy_now()

    assert requests == [True]
    assert not window._tray.action_deploy.isEnabled()
    assert "正在" in window._tray.action_deploy.text()

    window._on_manual_deployment_completed(True, "部署完成")

    assert notices and "部署完成" in notices[-1]
    assert "正在" not in window._tray.action_deploy.text()
    window.dispose()
    qapp.processEvents()


def test_rime_directory_change_is_reverted_when_staged_edits_are_kept(
        qapp, app_context, monkeypatch, tmp_path) -> None:
    from src.ui.main_window import MainWindow

    app_context.settings.auto_group_done = True
    window = MainWindow(app_context, background_tasks_enabled=False)
    original = app_context.settings.rime_dir
    replacement = str(tmp_path / "other-rime")
    app_context.settings.rime_dir = replacement
    monkeypatch.setattr(
        window._phrase_manager, "confirm_discard_changes", lambda _action: False,
    )
    rebuilds = []
    monkeypatch.setattr(app_context, "rebuild_repos", lambda: rebuilds.append(True))

    window._on_rime_dir_changed(replacement)

    assert app_context.settings.rime_dir == original
    assert rebuilds == []
    window.dispose()
    qapp.processEvents()


def test_main_window_title_and_status_show_index_build_progress(
        qapp, app_context) -> None:
    from src.ui.main_window import MainWindow

    app_context.settings.auto_group_done = True
    index = app_context.system_dictionary_index
    index._state = "building"
    index._scheme_id = "wanxiang"
    index._progress = {
        "phase": "building", "files_completed": 2, "files_total": 10,
        "current_file": "base.dict.yaml",
        "processed_bytes": 40, "total_bytes": 100,
    }
    window = MainWindow(app_context, background_tasks_enabled=False)

    window._refresh_index_status()

    assert "正在构建万象索引 38%" in window.windowTitle()
    assert window._index_progress.value() == 38
    assert "base.dict.yaml" in window._index_status_label.text()
    assert window.statusBar().isHidden() is False
    window._index_status_timer.stop()
    window.dispose()
    qapp.processEvents()


def test_result_toast_separates_phrase_and_codes(qapp) -> None:
    from src.ui.toast_notification import ToastNotification

    toast = ToastNotification()
    toast.show_result(True, "已收藏「银行」：yin'hang、yh。")

    assert toast._detail.isVisible()
    assert toast._detail_text.text() == "「银行」"
    assert "yin'hang" in toast._detail_codes.text()
    assert toast._detail_codes.wordWrap() is True
    assert toast._countdown.text() == "5 秒后关闭"
    assert toast._timer.interval() == 1000

    toast._tick()

    assert toast._countdown.text() == "4 秒后关闭"
    for _ in range(4):
        toast._tick()
    assert toast.isHidden()


def test_release_ico_is_loadable() -> None:
    """目录式发布包和系统托盘共用的 ICO 必须能被 Qt 读取。"""
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    icon = QIcon(str(icon_path))
    assert icon_path.is_file()
    assert not icon.isNull()
    assert not icon.pixmap(16, 16).isNull()

def test_settings_launches_input_diagnostics_only_on_click(qapp) -> None:
    from src.ui.settings_widget import SettingsWidget

    calls = []
    widget = SettingsWidget(
        _FakeSettings(), _FakeAutostart(), _FakeDeploy(),
        diagnostics_launcher=lambda: (calls.append("started") or True, "已按需启动"),
    )

    assert calls == []
    assert widget._lbl_diagnostics_status.isHidden()
    widget._btn_input_diagnostics.click()
    assert calls == ["started"]
    assert widget._lbl_diagnostics_status.text() == "已按需启动"

def test_settings_updates_status_when_diagnostics_closes(qapp) -> None:
    from src.ui.settings_widget import SettingsWidget

    widget = SettingsWidget(
        _FakeSettings(), _FakeAutostart(), _FakeDeploy(),
        diagnostics_launcher=lambda: (True, "输入诊断程序已启动。", 123),
        diagnostics_process_checker=lambda _pid: False,
    )

    widget._btn_input_diagnostics.click()
    assert widget._diagnostics_poll.isActive()
    widget._poll_input_diagnostics()
    assert not widget._diagnostics_poll.isActive()
    assert widget._lbl_diagnostics_status.text() == "输入诊断已停止。"
