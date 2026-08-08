"""Regression coverage for low-overhead UI and preview-service paths."""
from __future__ import annotations

from time import perf_counter
import threading
from unittest.mock import MagicMock

from src.repo.phrase_repo import Phrase
from src.service.pinyin_display_store import PinyinDisplayStore
from src.service.pinyin_service import PinyinService
from src.service.rime_preview_service import RimePreviewService
from src.ui.symbols_config_widget import SymbolsConfigWidget


def test_editor_display_is_inferred_once_per_phrase(tmp_path, monkeypatch) -> None:
    store = PinyinDisplayStore(tmp_path, PinyinService())
    phrase = Phrase("想象", "xiangxiang")
    infer = MagicMock(return_value="xiang'xiang")
    monkeypatch.setattr("src.service.pinyin_display_store.infer_display_code", infer)

    assert store.editor_display_for(phrase) == "xiang'xiang"
    assert store.editor_display_for(phrase) == "xiang'xiang"
    # Subsequent repaint reads the editor cache instead of inferring again.
    assert infer.call_count == 1


def test_preview_service_does_not_close_while_busy(monkeypatch) -> None:
    service = RimePreviewService("C:/Rime")
    close = MagicMock()
    monkeypatch.setattr(service, "close", close)

    service._busy = True
    service._close_when_idle()
    close.assert_not_called()

    service._busy = False
    service._close_when_idle()
    close.assert_called_once()


def test_symbols_table_uses_text_actions(qapp, app_context) -> None:
    widget = SymbolsConfigWidget(
        app_context.symbols_repo, app_context.backup_service,
        app_context.settings, app_context.deploy_service,
    )
    assert widget._table.rowCount() > 0
    assert all(widget._table.cellWidget(row, 1) is None
               for row in range(widget._table.rowCount()))
    assert all(widget._table.item(row, 1).text() == "删除"
               for row in range(widget._table.rowCount()))

def test_quick_add_dispose_stops_background_work(qapp) -> None:
    from src.ui.quick_add_dialog import QuickAddDialog

    dialog = QuickAddDialog()
    dialog.dispose()

    assert dialog._disposed is True
    assert dialog._preview_panel._disposed is True
    assert not dialog._suggestion_result_timer.isActive()
    assert not dialog._dictionary_timer.isActive()

    dialog.close()


def test_preview_first_query_is_fast_then_debounced(qapp) -> None:
    from src.ui.rime_preview_panel import RimePreviewPanel

    service = RimePreviewService("", "")
    panel = RimePreviewPanel(service, reserve_space=True)
    panel.set_query("测试", "ceshi")
    assert panel._request_timer.interval() == 20

    panel.set_query("测试二", "ceshier")
    assert panel._request_timer.interval() == 120

    panel.dispose()
    assert not panel._request_timer.isActive()


def test_preview_engine_stays_warm_for_ten_minutes() -> None:
    service = RimePreviewService("C:/Rime")
    assert service._idle_timeout_seconds == 10 * 60


def test_reentering_settings_tab_does_not_refresh_dictionary_catalog(qapp, app_context) -> None:
    from src.ui.main_window import MainWindow, TAB_LIBRARY, TAB_SETTINGS

    window = MainWindow(app_context)
    window._tabs.setCurrentIndex(TAB_SETTINGS)
    assert window._settings_widget is not None
    refresh = MagicMock()
    window._settings_widget.refresh = refresh

    window._tabs.setCurrentIndex(TAB_LIBRARY)
    window._tabs.setCurrentIndex(TAB_SETTINGS)

    refresh.assert_not_called()
    window.close()


def test_slow_dictionary_catalog_does_not_block_settings_construction(
    qapp, app_context, monkeypatch,
) -> None:
    from src.ui.settings_widget import SettingsWidget

    release = threading.Event()

    def slow_snapshot(_base):
        release.wait(2)
        return {"schemes": (), "catalogs": {}, "extensions": {
            "english": (), "mixed": (), "display": (),
        }}

    monkeypatch.setattr(SettingsWidget, "_build_dictionary_snapshot", slow_snapshot)
    started = perf_counter()
    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
    )
    elapsed = perf_counter() - started

    release.set()
    # The contract is that construction does not wait for the two-second scan.
    # Qt stylesheet polishing can vary substantially under the full Windows suite.
    assert elapsed < 1.5
    widget.close()


def test_settings_are_split_into_stable_task_sections(qapp, app_context) -> None:
    from src.ui.settings_widget import SettingsWidget

    widget = SettingsWidget(
        app_context.settings, app_context.autostart,
        app_context.deploy_service, app_context.schema_repo,
        backup=app_context.backup_service,
    )

    assert [
        widget._sections.tabText(index)
        for index in range(widget._sections.count())
    ] == ["常规", "词典与文件", "备份与恢复", "诊断与更新"]
    widget.close()
