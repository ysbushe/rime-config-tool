from __future__ import annotations

from src.service.backup_service import BackupService
from src.ui.settings_widget import SettingsWidget


class _Settings:
    def __init__(self, rime_dir: str) -> None:
        self.rime_dir = rime_dir
        self.auto_deploy = False
        self.hotkey_enabled = False
        self.hotkey_combo = "F2"
        self.deployer_path = ""
        self.backup_count = 5
        self.backup_dir = ""
        self.sandbox_mode = False
        self.autostart = False
        self.theme = "light"

    def set(self, key: str, value) -> None:
        setattr(self, key, value)


class _Autostart:
    enabled = False


class _Deploy:
    available = False
    deployer_path = ""

    def redetect(self) -> None:
        pass


def test_custom_backup_directory_is_used(temp_rime_dir, tmp_path) -> None:
    custom = tmp_path / "CustomBackups"
    service = BackupService(str(temp_rime_dir), keep=3, backup_dir=str(custom))

    saved = service.backup("custom_phrase.txt")

    assert saved is not None
    assert saved.parent == custom
    assert service.list_backups("custom_phrase.txt") == [saved]


def test_backup_path_is_recorded_in_ini(tmp_path, monkeypatch) -> None:
    settings_json = tmp_path / "settings.json"
    monkeypatch.setattr("src.settings.settings_path", lambda: settings_json)
    from src.settings import Settings

    settings = Settings()
    settings.backup_dir = str(tmp_path / "Backups")

    backup_ini = tmp_path / "backup.ini"
    assert backup_ini.is_file()
    assert str(tmp_path / "Backups") in backup_ini.read_text(encoding="utf-8")
    assert Settings().backup_dir == str(tmp_path / "Backups")


def test_phrase_restore_uses_preexisting_display_companion(
        qapp, temp_rime_dir, monkeypatch) -> None:
    phrase = temp_rime_dir / "custom_phrase.txt"
    display = temp_rime_dir / "pinyin_display.ini"
    phrase.write_text("原词条\tyuan\t1\n", encoding="utf-8")
    display.write_text("[old]\nvalue=1\n", encoding="utf-8")
    backup = BackupService(str(temp_rime_dir), keep=5)
    phrase_backup = backup.backup("custom_phrase.txt")
    display_backup = backup.backup("pinyin_display.ini")
    assert phrase_backup and display_backup

    phrase.write_text("新词条\txin\t1\n", encoding="utf-8")
    display.write_text("[new]\nvalue=2\n", encoding="utf-8")
    widget = SettingsWidget(
        _Settings(str(temp_rime_dir)), _Autostart(), _Deploy(), backup=backup)
    widget._restore_file.setCurrentIndex(
        widget._restore_file.findData("custom_phrase.txt"))
    widget._refresh_backup_versions()
    widget._restore_version.setCurrentIndex(
        widget._restore_version.findData(str(phrase_backup)))
    monkeypatch.setattr(
        "src.ui.settings_widget.QMessageBox.question",
        lambda *args: __import__("PySide6.QtWidgets", fromlist=["QMessageBox"])
        .QMessageBox.StandardButton.Yes,
    )

    widget._on_restore_backup()

    assert phrase.read_text(encoding="utf-8") == "原词条\tyuan\t1\n"
    assert display.read_text(encoding="utf-8") == "[old]\nvalue=1\n"
