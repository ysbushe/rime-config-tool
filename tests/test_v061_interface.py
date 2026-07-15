"""Focused coverage for v0.6.1 layout and release-default repairs."""
from __future__ import annotations

from src.repo.phrase_repo import Phrase
from src.service.backup_service import BackupService
from src.service.pinyin_service import PinyinService
from src.ui.multi_code_editor import MultiCodeEditor


def test_release_default_backup_uses_documents(temp_rime_dir, monkeypatch) -> None:
    monkeypatch.setattr("src.service.backup_service.sys.frozen", True, raising=False)
    service = BackupService(str(temp_rime_dir))
    assert service.using_default_backup_dir
    assert service.backup_dir.name == "Backups"
    assert "Documents" in str(service.backup_dir)


def test_multi_code_editor_uses_sectioned_workbench(qapp, phrase_repo) -> None:
    phrase = Phrase("测试", "ce'shi", 1)
    editor = MultiCodeEditor(
        phrase.text, [phrase], phrase_repo, PinyinService(),
        lambda item: item.code, groups=["其他"], group="其他",
    )
    assert editor.objectName() == "AppDialog"
    assert editor.findChildren(type(editor._preview_panel))
    assert [label.text() for label in editor.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel)
            if label.objectName() == "MultiCodeSectionTitle"] == ["词条信息", "已有编码", "待选编码"]
    assert editor._btn_add.text() == "+ 新增编码"
