"""设置交互、热键事务与删除确认框回归测试。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QGroupBox

from src.service.hotkey_backends import keyboard_backend, win32_backend
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.click_activated_spin import ClickActivatedSpinBox
from src.ui.delete_confirm_dialog import DeleteConfirmDialog
from src.ui.main_window import MainWindow
from src.ui.settings_widget import SettingsWidget


class _Settings:
    def __init__(self) -> None:
        self.rime_dir = "C:/Users/test/AppData/Roaming/Rime"
        self.auto_deploy = False
        self.hotkey_enabled = True
        self.hotkey_combo = "Ctrl+Alt+Q"
        self.deployer_path = "C:/Rime/WeaselDeployer.exe"
        self.backup_count = 5
        self.sandbox_mode = False
        self.autostart = False
        self.theme = "light"

    def set(self, key: str, value) -> None:
        setattr(self, key, value)


class _Autostart:
    enabled = False

    def enable(self) -> bool:
        self.enabled = True
        return True

    def disable(self) -> bool:
        self.enabled = False
        return True


class _Deploy:
    available = True
    deployer_path = "C:/Rime/WeaselDeployer.exe"

    def redetect(self) -> None:
        pass

    def deploy(self):
        return True, "ok"


class _IgnoredWheel:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


def test_internal_workdir_text_is_rejected_by_both_backends(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    current = str(tmp_path)
    assert win32_backend._is_internal_workdir_text(current)
    assert keyboard_backend._is_internal_workdir_text(current.replace("\\", "/"))
    assert win32_backend._is_internal_workdir_text(f'  "{current}\\"  ')
    assert keyboard_backend._is_internal_workdir_text(f"'{current.replace('\\', '/')}/'")
    assert not win32_backend._is_internal_workdir_text(str(tmp_path / "other"))


def test_settings_group_order(qapp) -> None:
    widget = SettingsWidget(_Settings(), _Autostart(), _Deploy())
    content_layout = widget._scroll.widget().layout()
    titles = []
    for index in range(content_layout.count()):
        child = content_layout.itemAt(index).widget()
        if isinstance(child, QGroupBox):
            titles.append(child.title())

    assert titles == [
        "Rime 目录与部署",
        "行为",
        "词库维护",
        "GitHub 更新",
        "备份与恢复",
        "开机自启",
        "受管文件",
        "扩展词库（只读检测）",
        "方案信息（只读）",
    ]


def test_theme_cards_emit_and_update_selection(qapp) -> None:
    settings = _Settings()
    settings.theme = "dark"
    widget = SettingsWidget(settings, _Autostart(), _Deploy())
    widget.show()
    qapp.processEvents()

    emitted = []
    widget.themeChanged.connect(emitted.append)

    assert set(widget._theme_cards) == {"light", "dark", "ink"}
    assert widget._theme_cards["dark"].property("selected") is True

    QTest.mouseClick(widget._theme_cards["ink"], Qt.MouseButton.LeftButton)

    assert emitted == ["ink"]
    assert widget._theme_cards["ink"].property("selected") is True
    assert widget._theme_cards["dark"].property("selected") is False


def test_guarded_combo_requires_click_before_wheel(qapp) -> None:
    combo = ClickActivatedComboBox()
    combo.addItems(["1", "2", "3"])
    combo.show()
    qapp.processEvents()

    wheel = _IgnoredWheel()
    combo.wheelEvent(wheel)
    assert wheel.ignored is True
    assert combo._wheel_armed is False

    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    assert combo._wheel_armed is True
    combo.hidePopup()

    qapp.sendEvent(combo, QFocusEvent(QEvent.Type.FocusOut))
    assert combo._wheel_armed is False


def _fake_main_window(register_results):
    settings = SimpleNamespace(
        hotkey_combo="Ctrl+Alt+Q",
        hotkey_enabled=True,
    )
    hotkey_manager = MagicMock()
    status_widget = MagicMock()
    tray = MagicMock()
    results = iter(register_results)
    window = SimpleNamespace(
        _ctx=SimpleNamespace(settings=settings, hotkey_manager=hotkey_manager),
        _settings_widget=status_widget,
        _tray=tray,
        _register_hotkey=lambda: next(results),
    )
    return window, settings, hotkey_manager, status_widget, tray


def test_hotkey_apply_success_shows_feedback() -> None:
    window, settings, manager, status, tray = _fake_main_window([True])

    MainWindow._on_hotkey_combo_changed(window, "Ctrl+Shift+Q")

    assert settings.hotkey_combo == "Ctrl+Shift+Q"
    manager.unregister.assert_called_once()
    status.show_hotkey_apply_result.assert_called_once_with(
        True,
        "热键已应用：Ctrl+Shift+Q",
        applied_combo="Ctrl+Shift+Q",
    )
    tray.set_hotkey_state.assert_called_with(True)


def test_hotkey_apply_failure_restores_previous_combo() -> None:
    window, settings, manager, status, tray = _fake_main_window([False, True])

    MainWindow._on_hotkey_combo_changed(window, "Ctrl+Shift+Q")

    assert settings.hotkey_combo == "Ctrl+Alt+Q"
    manager.unregister.assert_called_once()
    args, kwargs = status.show_hotkey_apply_result.call_args
    assert args[0] is False
    assert "已恢复原热键 Ctrl+Alt+Q" in args[1]
    assert kwargs["applied_combo"] == "Ctrl+Alt+Q"
    tray.set_hotkey_state.assert_called_with(True)


def test_delete_dialog_buttons_are_equal_and_no_is_default(qapp) -> None:
    dialog = DeleteConfirmDialog("删除", "确认删除？")
    rejected = QSignalSpy(dialog.rejected)
    dialog.show()
    qapp.processEvents()

    assert abs(dialog.yes_button.width() - dialog.no_button.width()) <= 1
    assert dialog.no_button.isDefault()
    assert dialog.no_button.hasFocus()
    assert dialog.yes_button.objectName() == "Danger"
    assert dialog.no_button.objectName() == "Primary"

    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert rejected.count() == 1


def test_settings_refresh_does_not_toggle_autostart(qapp) -> None:
    class TrackingAutostart(_Autostart):
        enabled = True

        def __init__(self) -> None:
            self.enable_calls = 0
            self.disable_calls = 0

        def enable(self) -> bool:
            self.enable_calls += 1
            self.enabled = True
            return True

        def disable(self) -> bool:
            self.disable_calls += 1
            self.enabled = False
            return True

    autostart = TrackingAutostart()
    SettingsWidget(_Settings(), autostart, _Deploy())
    assert autostart.enable_calls == 0
    assert autostart.disable_calls == 0


def test_guarded_spin_requires_click_before_wheel(qapp) -> None:
    spin = ClickActivatedSpinBox()
    spin.setRange(1, 365)
    spin.setValue(7)
    spin.show()
    qapp.processEvents()

    wheel = _IgnoredWheel()
    spin.wheelEvent(wheel)
    assert wheel.ignored is True
    assert spin.value() == 7

    QTest.mouseClick(spin, Qt.MouseButton.LeftButton)
    assert spin._wheel_armed is True


def test_backup_policy_requires_explicit_save(qapp) -> None:
    settings = _Settings()
    widget = SettingsWidget(settings, _Autostart(), _Deploy())
    widget._backup_combo.setCurrentText("3")
    widget._cb_scheduled_backup.setChecked(True)
    widget._backup_interval.setValue(30)
    widget._cb_backup_cleanup.setChecked(False)

    assert settings.backup_count == 5
    assert not hasattr(settings, "scheduled_backup_enabled")

    widget._save_backup_settings()

    assert settings.backup_count == 3
    assert settings.scheduled_backup_enabled is True
    assert settings.backup_interval_days == 30
    assert settings.backup_auto_cleanup is False
    assert "已保存" in widget._lbl_backup_status.text()


def test_restore_selectors_use_click_guard(qapp) -> None:
    widget = SettingsWidget(_Settings(), _Autostart(), _Deploy())
    assert isinstance(widget._restore_file, ClickActivatedComboBox)
    assert isinstance(widget._restore_version, ClickActivatedComboBox)


def test_settings_checkboxes_toggle_visibly(qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from src.ui.settings_widget import SettingsWidget
    from src.ui.visible_checkbox import VisibleCheckBox

    settings = _Settings()
    widget = SettingsWidget(settings, _Autostart(), _Deploy())
    widget.show()
    qapp.processEvents()
    checkbox = widget._cb_autodeploy
    assert isinstance(checkbox, VisibleCheckBox)
    before = checkbox.isChecked()
    checkbox.click()
    qapp.processEvents()
    assert checkbox.isChecked() is not before
