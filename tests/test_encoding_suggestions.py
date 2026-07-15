from __future__ import annotations

from src.encoding.code_suggestions import (
    build_suggestions,
    infer_display_code,
    normalize_display_code,
    raw_code,
)
from src.repo.phrase_repo import Phrase
from src.service.pinyin_display_store import PinyinDisplayStore
from src.service.pinyin_service import PinyinService
from src.ui.quick_add_dialog import QuickAddDialog
from PySide6.QtWidgets import QLabel

from src.ui.phrase_editor import PhraseEditor
from src.ui.multi_code_editor import MultiCodeEditor


def test_separator_aliases_are_normalized_and_removed_for_storage() -> None:
    assert normalize_display_code("yin’‘`hang") == "yin'hang"
    assert raw_code("yin'hang") == "yinhang"


def test_suggestions_cover_full_strict_compact_and_mixed() -> None:
    suggestions = build_suggestions("银行", PinyinService())
    labels = [item.label for item in suggestions]
    assert labels == ["全拼", "严格简拼", "紧凑简拼", "混剪简拼"]
    assert suggestions[0].display_code == "yin'hang"
    assert suggestions[1].raw_code == "yh"


def test_mixed_chinese_english_and_digits_preserve_ascii_runs() -> None:
    suggestions = build_suggestions("微信Pro11", PinyinService())
    assert suggestions[0].display_code == "wei'xin'pro11"
    assert suggestions[0].raw_code == "weixinpro11"


def test_old_entries_infer_readable_boundaries() -> None:
    pinyin = PinyinService()
    assert infer_display_code("银行", "yinhang", pinyin) == "yin'hang"
    assert infer_display_code("银行", "yh", pinyin) == "y'h"
    assert infer_display_code("银行", "yinh", pinyin) == "yin'h"


def test_display_store_caches_inferred_codes(tmp_path) -> None:
    class CountingPinyin:
        def __init__(self) -> None:
            self.calls = 0

        def get_pinyin_units(self, text: str) -> list[str]:
            self.calls += 1
            return ["yin", "hang"]

    pinyin = CountingPinyin()
    store = PinyinDisplayStore(tmp_path, pinyin)
    phrase = Phrase("银行", "yinhang", 1)

    assert store.display_for(phrase) == "yin'hang"
    assert store.display_for(phrase) == "yin'hang"
    assert pinyin.calls == 1


def test_display_store_roundtrip_and_prune(tmp_path) -> None:
    pinyin = PinyinService()
    store = PinyinDisplayStore(tmp_path, pinyin)
    store.set("银行", "yh", "y'h")
    store.save()

    loaded = PinyinDisplayStore(tmp_path, pinyin)
    phrase = Phrase("银行", "yh", 3)
    assert loaded.display_for(phrase) == "y'h"
    loaded.prune([])
    loaded.save()
    assert "银行" not in (tmp_path / "pinyin_display.ini").read_text(encoding="utf-8")


def test_quick_add_separator_button_and_storage_value(qapp) -> None:
    dialog = QuickAddDialog(prefill_text="银行")
    dialog._code.setText("yin’hang")
    dialog._normalize_code_input(dialog._code.text())
    assert dialog._code.text() == "yin'hang"
    assert dialog.get_values()["code"] == "yinhang"

    dialog._code.setText("yinhang")
    dialog._code.setCursorPosition(3)
    dialog._insert_separator()
    assert dialog._code.text() == "yin'hang"


def test_phrase_editor_constructs_with_real_repo(qapp, phrase_repo) -> None:
    editor = PhraseEditor(repo=phrase_repo)

    assert editor.windowTitle() == "新增词条"
    editor._text.setText("银行")
    editor._on_text_edited("银行")
    assert editor._suggestion_panel._buttons


def test_english_suggestions_offer_case_and_compact_phrase_forms() -> None:
    suggestions = build_suggestions("Open-AI Tools", PinyinService())
    assert [(item.label, item.display_code) for item in suggestions] == [
        ("英文小写", "openaitools"),
        ("英文大写", "OPENAITOOLS"),
        ("原样保留", "OpenAITools"),
    ]


def test_suggestion_rows_expose_name_code_and_colored_state(qapp, phrase_repo) -> None:
    editor = PhraseEditor(repo=phrase_repo)
    editor._text.setText("银行")
    editor._on_text_edited("银行")
    option = editor._suggestion_panel._buttons[0]

    assert option.objectName() == "EncodingSuggestion"
    assert option.findChild(QLabel, "SuggestionName") is not None
    assert option.findChild(QLabel, "SuggestionCode") is not None
    assert option.findChild(QLabel, "SuggestionState") is not None
    assert option.property("state") in {"success", "warning", "duplicate"}


def test_quick_add_collects_extra_codes(qapp) -> None:
    dialog = QuickAddDialog(prefill_text="银行")
    dialog._add_code("y'h")
    dialog._add_current_code()

    values = dialog.get_values()

    assert "y'h" in values["additional_codes"]
    assert dialog._extra_code_layout.count() == len(values["additional_codes"])
    dialog._remove_code("y'h")
    assert "y'h" not in dialog.get_values()["additional_codes"]


def test_phrase_editor_collects_and_removes_extra_codes(qapp, phrase_repo) -> None:
    editor = PhraseEditor(repo=phrase_repo)
    editor._text.setText("银行")
    editor._on_text_edited("银行")
    editor._add_code("y'h")

    assert editor.get_values()["additional_codes"] == ["y'h"]
    assert editor._extra_code_layout.count() == 1

    editor._remove_code("y'h")
    assert editor.get_values()["additional_codes"] == []


def test_optional_uppercase_english_output_keeps_lowercase_code(qapp) -> None:
    dialog = QuickAddDialog(prefill_text="gui")
    dialog._english_upper.setChecked(True)
    values = dialog.get_values()

    assert values["text"] == "GUI"
    assert values["code"] == "gui"

    editor = PhraseEditor(repo=None)
    editor._text.setText("api")
    editor._on_text_edited("api")
    editor._code.setText("API")
    editor._english_upper.setChecked(True)
    values = editor.get_values()
    assert values["text"] == "API"
    assert values["code"] == "api"


def test_multi_code_editor_text_is_editable(qapp, phrase_repo) -> None:
    phrase = Phrase("旧文本", "jiuwen", 1)
    editor = MultiCodeEditor("旧文本", [phrase], phrase_repo, PinyinService(), lambda p: p.code)
    editor._text.setText("新文本")

    assert editor.text_value() == "新文本"
    assert editor._text.isReadOnly() is False


def test_multi_code_suggestion_add_immediately_updates_candidates(qapp, phrase_repo) -> None:
    editor = MultiCodeEditor("银行", [Phrase("银行", "yinhang", 1)], phrase_repo, PinyinService(), lambda p: p.code)
    before = [option._code for option in editor._suggestions._buttons]
    assert "yh" in [code.replace("'", "") for code in before]

    editor._add_suggested_code("y'h")

    assert len(editor._rows) == 2
    assert "yh" not in [code.replace("'", "") for code in [option._code for option in editor._suggestions._buttons]]

    editor._remove_row(editor._rows[-1][0].parentWidget())
    assert "yh" in [code.replace("'", "") for code in [option._code for option in editor._suggestions._buttons]]


def test_multi_code_candidates_clear_without_leftover_widgets(qapp, phrase_repo) -> None:
    editor = MultiCodeEditor("银行", [Phrase("银行", "yinhang", 1)], phrase_repo, PinyinService(), lambda p: p.code)
    for option in list(editor._suggestions._buttons):
        editor._add_suggested_code(option._code)
    qapp.processEvents()

    assert editor._suggestions._suggestions.count() == 0


def test_delete_code_dialog_uses_aligned_grid(qapp) -> None:
    from src.ui.code_delete_dialog import CodeDeleteDialog

    dialog = CodeDeleteDialog("对下", [Phrase("对下", "duixia", 1), Phrase("对下", "duix", 1)], "duix")

    assert dialog.selected()[0].code == "duix"
    choices = dialog.layout().itemAt(0).widget()
    assert choices.objectName() == "DialogSection"


def test_delete_code_row_click_toggles_selection(qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from src.ui.code_delete_dialog import CodeDeleteDialog

    dialog = CodeDeleteDialog("对下", [Phrase("对下", "duixia", 1)], "duixia")
    row = dialog._checks[0][1].parentWidget()
    assert dialog.selected()

    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert dialog.selected() == []
