from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QInputMethodEvent

from src.repo.phrase_repo import PhraseRepo
from src.service.pinyin_service import PinyinService
from src.service.rime_preview_service import (
    PreviewCandidate,
    PreviewEvents,
    PreviewSnapshot,
)
from src.service.system_dictionary_index import DictionaryCandidate
from src.ui.quick_tune_dialog import QuickTuneDialog


def _repo(tmp_path) -> PhraseRepo:
    path = tmp_path / "custom_phrase.txt"
    path.write_text(
        "候选甲\ttongma\t9\n候选乙\ttongma\t5\n银行\tyinhang\t4\n"
        "急什么\tjism\t1\n柱脚\tzhujiao\t1\n",
        encoding="utf-8",
    )
    return PhraseRepo(str(path))


def _visible_labels(dialog: QuickTuneDialog) -> list[str]:
    return [button.text() for button in dialog._preview_buttons if not button.isHidden()]


def _save_ok(_entries, _signature) -> tuple[bool, str]:
    return True, "已保存"


def _set_search(dialog: QuickTuneDialog, value: str) -> None:
    dialog._search.setText(value)
    dialog._search_timer.stop()
    dialog._refresh_table()


def test_code_input_stages_rank_until_save_is_clicked(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    calls = []

    def save_changes(entries, signature) -> tuple[bool, str]:
        calls.append((entries, signature))
        return True, "已保存"

    dialog = QuickTuneDialog(repo, PinyinService(), save_changes=save_changes)
    _set_search(dialog, "tongma")

    assert dialog._table.rowCount() == 2
    assert not dialog._up_button.isEnabled()
    dialog._table.selectRow(1)
    assert dialog._up_button.isEnabled()
    dialog._up_button.click()
    assert calls == []
    assert repo.find("候选乙", "tongma").weight == 5
    assert dialog._save_button.isEnabled()
    dialog._save_button.click()
    assert len(calls) == 1
    saved_entries, _signature = calls[0]
    assert next(
        item for item in saved_entries
        if item.text == "候选乙" and item.code == "tongma"
    ).weight == 9
    dialog.close()


def test_failed_save_keeps_staged_rank_for_retry(qapp, tmp_path) -> None:
    attempts = 0

    def fail_once(_entries, _signature):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("locked")
        return True, "已保存"

    dialog = QuickTuneDialog(
        _repo(tmp_path), PinyinService(), save_changes=fail_once,
    )
    _set_search(dialog, "tongma")
    dialog._table.selectRow(1)
    dialog._up_button.click()

    dialog._save_button.click()

    assert dialog._dirty is True
    assert dialog._save_button.isEnabled()
    assert "已保留当前暂存调整" in dialog._status.text()

    dialog._save_button.click()

    assert dialog._dirty is False
    assert not dialog._save_button.isEnabled()
    dialog.close()


def test_closing_without_save_discards_staged_rank(qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    before = repo.path.read_bytes()
    calls = []
    dialog = QuickTuneDialog(
        repo, PinyinService(),
        save_changes=lambda entries, signature:
        (calls.append((entries, signature)) is None, "saved"),
    )
    _set_search(dialog, "tongma")
    dialog._table.selectRow(1)
    dialog._top_button.click()

    dialog.close()

    assert calls == []
    assert repo.path.read_bytes() == before
    assert repo.find("候选乙", "tongma").weight == 5


def test_dialog_uses_one_exact_search_and_read_only_selected_fields(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(repo, PinyinService())

    assert dialog._code_input.isReadOnly()
    assert dialog._text_input.isReadOnly()
    _set_search(dialog, "银行")

    assert dialog._table.rowCount() >= 1
    dialog._table.selectRow(0)
    assert dialog._code_input.text() == "yinhang"
    assert dialog._text_input.text() == "银行"
    assert _visible_labels(dialog) == []
    dialog.close()


def test_existing_phrase_selection_populates_both_inputs(qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    phrase = repo.find("候选乙", "tongma")
    assert phrase is not None
    dialog = QuickTuneDialog(repo, PinyinService(), initial_phrase=phrase)

    assert dialog._text_input.text() == "候选乙"
    assert dialog._code_input.text() == "tongma"
    assert dialog._selected_key == phrase.key
    dialog.close()


def test_clicking_personal_candidate_reuses_table_and_preview_widgets(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(
        repo, PinyinService(),
        save_changes=_save_ok,
    )
    _set_search(dialog, "tongma")
    table_item = dialog._table.item(0, 0)
    preview_buttons = tuple(dialog._preview_buttons)
    preview_height = dialog._candidate_box.height()

    dialog._table.selectRow(1)

    assert dialog._table.item(0, 0) is table_item
    assert tuple(dialog._preview_buttons) == preview_buttons
    assert dialog._candidate_box.height() == preview_height
    dialog.close()


class _PreviewService:
    def __init__(self) -> None:
        self.events = PreviewEvents()
        self.page_size = 6
        self.schema_id = "test"
        self.availability_message = ""
        self.snapshot = PreviewSnapshot(
            "ready",
            "tongma",
            (PreviewCandidate("系统候选"), PreviewCandidate("候选乙")),
        )

    def request(self, _code: str) -> None:
        self.events.changed.emit()


def test_actual_rime_preview_allows_system_candidate_override(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    service = _PreviewService()
    calls = []
    dialog = QuickTuneDialog(
        repo, PinyinService(), preview_service=service,
        save_changes=lambda entries, signature:
        (calls.append((entries, signature)) is None, "ok"),
    )
    dialog._preview_from_code("tongma")
    dialog._refresh_engine_preview()

    assert _visible_labels(dialog) == [
        "1. 系统候选", "2. 候选乙",
    ]
    dialog._preview_buttons[0].click()
    assert dialog._top_button.isEnabled()
    dialog._top_button.click()
    assert calls == []
    dialog._save_button.click()
    assert len(calls) == 1
    assert any(item.text == "系统候选" for item in calls[0][0])
    dialog._preview_buttons[1].click()
    assert dialog._top_button.isEnabled()
    dialog.close()


class _DictionaryIndex:
    state = "ready"

    def lookup_text(self, text: str, limit: int = 20):
        if text == "了":
            return [DictionaryCandidate("了", "le", "wanxiang", 1000, 1.2)]
        if text == "物体":
            return [DictionaryCandidate("物体", "wuti", "wanxiang", 800, 1.2)]
        return []

    def lookup_code(self, code: str, limit: int = 20):
        if code == "le":
            return [DictionaryCandidate("了", "le", "wanxiang", 1000, 1.2)]
        return []

    def lookup_generated_code(self, code: str, _splitter, limit: int = 20):
        if code == "h":
            return [DictionaryCandidate("还", "h", "wanxiang", 900, 1.2)]
        if code in {"wut", "wti"}:
            return [DictionaryCandidate("物体", code, "wanxiang", 800, 1.2)]
        return []


def test_top_search_includes_wanxiang_and_selects_external_word(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(
        repo, PinyinService(), dictionary_index=_DictionaryIndex(),
        save_changes=_save_ok,
    )

    _set_search(dialog, "了")

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "了"
    assert dialog._table.item(0, 1).text() == "le"
    assert dialog._table.item(0, 2).text() == "万象词库"
    dialog._table.selectRow(0)
    assert dialog._text_input.text() == "了"
    assert dialog._code_input.text() == "le"
    assert dialog._top_button.isEnabled()
    dialog.close()


def test_top_search_uses_exact_text_or_exact_normalized_code(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(repo, PinyinService())

    _set_search(dialog, "sm")
    assert dialog._table.rowCount() == 0

    _set_search(dialog, "zh")
    assert dialog._table.rowCount() == 0

    _set_search(dialog, "jism")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "急什么"

    _set_search(dialog, "急什么")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 1).text() == "jism"
    dialog.close()


def test_top_search_includes_supported_generated_codes_both_directions(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(
        repo, PinyinService(), dictionary_index=_DictionaryIndex()
    )

    _set_search(dialog, "物体")
    codes = {dialog._table.item(row, 1).text() for row in range(dialog._table.rowCount())}
    assert {"wuti", "wut", "wti"}.issubset(codes)

    _set_search(dialog, "wut")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "物体"

    _set_search(dialog, "h")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "还"
    dialog.close()


def test_single_letter_code_does_not_create_fake_pinyin_suggestions(
        qapp, tmp_path) -> None:
    class LetterDictionaryIndex(_DictionaryIndex):
        def lookup_code(self, code: str, limit: int = 20):
            if code in {"h", "q"}:
                return [
                    DictionaryCandidate(
                        code.upper(), code, "melt_eng", 10, 1.0
                    )
                ]
            return super().lookup_code(code, limit)

    dialog = QuickTuneDialog(
        _repo(tmp_path), PinyinService(),
        dictionary_index=LetterDictionaryIndex(),
    )

    for code in ("h", "q"):
        _set_search(dialog, code)
        sources = {
            dialog._table.item(row, 2).text()
            for row in range(dialog._table.rowCount())
        }
        pairs = {
            (
                dialog._table.item(row, 0).text(),
                dialog._table.item(row, 1).text(),
            )
            for row in range(dialog._table.rowCount())
        }
        assert "拼音建议" not in sources
        assert (code.upper(), code) in pairs

    dialog.close()


def test_changing_search_clears_stale_row_and_preview_selection(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(
        repo, PinyinService(),
        save_changes=_save_ok,
    )
    dialog._table.selectRow(0)
    assert dialog._code_input.text() == "tongma"
    assert dialog._top_button.isEnabled()

    _set_search(dialog, "sm")

    assert dialog._table.rowCount() == 0
    assert dialog._table.currentRow() == -1
    assert dialog._code_input.text() == ""
    assert dialog._text_input.text() == ""
    assert not any(not button.isHidden() for button in dialog._preview_buttons)
    assert not dialog._top_button.isEnabled()
    dialog.close()


def test_ime_preedit_immediately_clears_stale_search_rows(
        qapp, tmp_path) -> None:
    dialog = QuickTuneDialog(_repo(tmp_path), PinyinService())
    _set_search(dialog, "jism")
    assert dialog._table.rowCount() == 1

    QCoreApplication.sendEvent(dialog._search, QInputMethodEvent("sm", []))

    assert dialog._table.rowCount() == 0
    assert dialog._table.currentRow() == -1
    dialog.close()


def test_query_and_preview_sections_do_not_move_when_content_changes(
        qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    dialog = QuickTuneDialog(
        repo, PinyinService(), dictionary_index=_SixCandidateIndex(),
        save_changes=_save_ok,
    )
    dialog.show()
    qapp.processEvents()
    query_geometry = dialog._query_section.geometry()
    preview_geometry = dialog._preview_section.geometry()

    _set_search(dialog, "了")
    dialog._preview_from_code("liu")
    dialog._preview_note.setText(
        "这是一段故意较长的状态说明，用于验证说明文字换行、候选数量变化和表格结果变化时，"
        "输入区域与实时预览区域仍保持固定位置和固定高度。"
    )
    dialog._set_status("较长的底部状态信息也不应推动上方两个分区。", "info")
    qapp.processEvents()

    assert dialog._query_section.geometry() == query_geometry
    assert dialog._preview_section.geometry() == preview_geometry
    dialog.close()


class _SixCandidateIndex(_DictionaryIndex):
    def lookup_code(self, code: str, limit: int = 20):
        return [
            DictionaryCandidate(f"候选{index}", code, "wanxiang", 100 - index, 1.2)
            for index in range(1, 9)
        ]


def test_preview_uses_current_rime_page_size(qapp, tmp_path) -> None:
    repo = _repo(tmp_path)
    service = _PreviewService()
    service.snapshot = PreviewSnapshot(
        "ready", "liu",
        tuple(PreviewCandidate(f"实际候选{index}") for index in range(1, 9)),
    )
    dialog = QuickTuneDialog(
        repo, PinyinService(), preview_service=service,
        dictionary_index=_SixCandidateIndex(),
    )

    dialog._preview_from_code("liu")
    dialog._refresh_engine_preview()

    assert len(_visible_labels(dialog)) == 6
    assert _visible_labels(dialog)[-1] == "6. 实际候选6"
    dialog.close()


def test_unavailable_actual_preview_never_substitutes_static_candidates(
        qapp, tmp_path) -> None:
    service = _PreviewService()
    service.snapshot = PreviewSnapshot("unavailable", "liu", message="test")
    dialog = QuickTuneDialog(
        _repo(tmp_path), PinyinService(), preview_service=service,
        dictionary_index=_SixCandidateIndex(),
    )

    dialog._preview_from_code("liu")
    dialog._refresh_engine_preview()

    assert _visible_labels(dialog) == []
    assert "静态词典结果不会替代显示" in dialog._preview_note.text()
    dialog.close()
