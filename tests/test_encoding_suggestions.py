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
from src.ui.phrase_editor import PhraseEditor


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
