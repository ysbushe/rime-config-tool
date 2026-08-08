from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from src.repo.phrase_repo import Phrase
from src.service.library_tools import health_check
from src.ui.library_dialogs import LibraryHealthDialog, PhraseSearchResultDialog


def test_health_dialog_exposes_view_actions_and_double_click(qapp) -> None:
    phrases = [
        Phrase("甲", "jia", 1), Phrase("甲", "jia", 8),
        Phrase("乙", "yi", 2), Phrase("乙", "yi", 3),
        Phrase("", "", 1),
    ]
    viewed = []
    dialog = LibraryHealthDialog(
        phrases, health_check(phrases),
        lambda text, code: viewed.append((text, code)),
    )

    assert dialog._duplicates.rowCount() == 2
    assert not hasattr(dialog, "_merge_all")
    for row in range(2):
        button = dialog._duplicates.cellWidget(row, 4)
        assert isinstance(button, QPushButton)
        assert button.text() == "查看"

    dialog._duplicates.cellWidget(0, 4).click()
    dialog._view_duplicate_row(dialog._duplicates.model().index(1, 0))

    assert viewed == [("甲", "jia"), ("乙", "yi")]


def test_search_result_reuses_edit_delete_actions_and_refreshes(qapp) -> None:
    phrases = [Phrase("甲", "jia", 1), Phrase("甲", "jia", 8)]
    edited = []
    deleted = []
    dialog = PhraseSearchResultDialog(
        "查看词条：甲", lambda: phrases,
        lambda phrase: edited.append(phrase),
        lambda phrase: deleted.append(phrase),
    )

    assert dialog._table.model().rowCount() == 2
    dialog._edit(phrases[0])
    dialog._delete(phrases[1])

    assert edited == [phrases[0]]
    assert deleted == [phrases[1]]
    assert "共找到 2 条" in dialog._summary.text()