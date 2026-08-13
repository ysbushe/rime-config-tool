"""Focused coverage for v0.6.1 layout and release-default repairs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from src.repo.phrase_repo import Phrase
from src.service.backup_service import BackupService
from src.service.pinyin_service import PinyinService
from src.ui.encoding_suggestion_panel import EncodingSuggestionOption
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


def test_multi_code_preview_and_suggestion_rows_are_aligned(qapp, phrase_repo) -> None:
    phrase = Phrase("同一", "tong'yi", 1)
    editor = MultiCodeEditor(
        phrase.text, [phrase], phrase_repo, PinyinService(),
        lambda item: item.code, groups=["其他"], group="其他",
    )
    editor.show()
    qapp.processEvents()

    preview = editor._preview_panel
    expected = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    assert int(preview._candidate_cells[0].alignment()) == expected
    assert int(preview._weight_cells[0].alignment()) == expected
    for option in editor.findChildren(EncodingSuggestionOption):
        button = option.findChild(QPushButton, "SuggestionAdd")
        assert button is not None
        assert abs(button.geometry().center().y() - option.rect().center().y()) <= 1

    editor.close()
