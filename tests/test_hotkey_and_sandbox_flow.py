"""热键提示与沙盒副本流程测试。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog

from src.service.sandbox_service import SandboxService
from src.ui.quick_add_dialog import QuickAddDialog
from src.ui.settings_widget import SettingsWidget


class _Settings:
    def __init__(self, rime_dir: str, sandbox_mode: bool = True) -> None:
        self.rime_dir = rime_dir
        self.sandbox_mode = sandbox_mode


def test_quick_add_notice_for_empty_hotkey_capture(qapp) -> None:
    dlg = QuickAddDialog(notice="未捕获到选中文本")

    # 无捕获提示使用克制的信息样式（_info），而非红色错误
    assert "未捕获" in dlg._info.text()
    assert dlg._err.text() == ""



def test_quick_add_weight_dropdown_defaults_and_custom(qapp, monkeypatch) -> None:
    dlg = QuickAddDialog(prefill_text="测试")

    # 下拉选项为 1 / 2 / 3 / 自定义…，默认权重 1
    labels = [dlg._weight_combo.itemText(i) for i in range(dlg._weight_combo.count())]
    assert labels == ["1", "2", "3", "自定义…"]
    assert dlg.get_values()["weight"] == 1

    dlg._weight_combo.setCurrentText("3")
    assert dlg.get_values()["weight"] == 3

    # 选『自定义…』弹出整数输入框；mock 确认返回 8
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (8, True))
    dlg._weight_combo.setCurrentIndex(3)
    assert dlg.get_values()["weight"] == 8
    assert dlg._weight_combo.currentText() == "自定义（8）"

    # 改回 3 后，再选自定义并取消 → 恢复为 3（不改动窗口内容/尺寸）
    dlg._weight_combo.setCurrentText("3")
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (0, False))
    dlg._weight_combo.setCurrentIndex(3)
    assert dlg.get_values()["weight"] == 3


class _WidgetSettings:
    def __init__(self, sandbox_mode: bool = True) -> None:
        self.rime_dir = "C:/Users/test/AppData/Roaming/Rime"
        self.auto_deploy = True
        self.hotkey_enabled = True
        self.hotkey_combo = "Ctrl+Alt+Q"
        self.deployer_path = "C:/Rime/WeaselDeployer.exe"
        self.backup_count = 5
        self.sandbox_mode = sandbox_mode
        self.autostart = False
        self.theme = "light"

    def set(self, key: str, value) -> None:
        setattr(self, key, value)


class _Autostart:
    enabled = False

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False


class _WidgetDeploy:
    available = True
    deployer_path = "C:/Rime/WeaselDeployer.exe"

    def redetect(self) -> None:
        pass

    def deploy(self) -> tuple[bool, str]:
        return True, "ok"


def test_settings_disables_manual_deploy_in_sandbox(qapp) -> None:
    widget = SettingsWidget(_WidgetSettings(), _Autostart(), _WidgetDeploy())

    assert not widget._btn_deploy.isEnabled()
    assert "沙盒模式" in widget._lbl_deploy.text()


def test_settings_uses_vertical_scroll_area(qapp) -> None:
    widget = SettingsWidget(_WidgetSettings(), _Autostart(), _WidgetDeploy())
    widget.resize(700, 320)
    widget.show()
    qapp.processEvents()

    assert widget._scroll.widgetResizable()
    assert widget._scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert widget._scroll.verticalScrollBar().maximum() > 0

def test_sandbox_copies_group_sidecar(temp_rime_dir, tmp_path, monkeypatch) -> None:
    sidecar = temp_rime_dir / "custom_phrase.txt.groups.json"
    sidecar.write_text('{"groups":["工作"],"membership":{}}', encoding="utf-8")
    monkeypatch.setattr(
        "src.service.sandbox_service.user_config_dir",
        lambda: Path(tmp_path) / "RimeConfig",
    )
    svc = SandboxService(_Settings(str(temp_rime_dir), sandbox_mode=True))

    sandbox_dir = Path(svc.active_rime_dir())

    assert (sandbox_dir / "custom_phrase.txt").exists()
    assert (sandbox_dir / "rime_frost.schema.yaml").exists()
    assert (sandbox_dir / "symbols_v.yaml").exists()
    assert (sandbox_dir / "custom_phrase.txt.groups.json").read_text(encoding="utf-8") == sidecar.read_text(encoding="utf-8")


def test_sandbox_first_entry_overwrites_newer_stale_copy(
        temp_rime_dir, tmp_path, monkeypatch) -> None:
    sandbox_root = tmp_path / "RimeConfig"
    sandbox_dir = sandbox_root / "sandbox"
    sandbox_dir.mkdir(parents=True)
    stale = sandbox_dir / "custom_phrase.txt"
    stale.write_text("测试残留\tstale\t1\n", encoding="utf-8")
    stale.touch()
    monkeypatch.setattr(
        "src.service.sandbox_service.user_config_dir",
        lambda: sandbox_root,
    )
    service = SandboxService(_Settings(str(temp_rime_dir), sandbox_mode=True))

    service.active_rime_dir()

    assert stale.read_text(encoding="utf-8") == (
        temp_rime_dir / "custom_phrase.txt").read_text(encoding="utf-8")
