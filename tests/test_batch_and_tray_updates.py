from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.repo.phrase_repo import Phrase
from src.ui.quick_add_dialog import QuickAddDialog
from src.ui.tray_icon import TrayIcon


def test_batch_select_button_and_group_action_are_visible(qapp, app_context):
    from src.ui.phrase_manager import PhraseManager

    manager = PhraseManager(
        app_context.phrase_repo, app_context.group_service, app_context.backup_service,
        app_context.settings, app_context.deploy_service, app_context.pinyin_service,
    )
    assert manager._btn_batch_select.parent() is not None
    manager._on_toggle_batch_select()
    assert not manager._btn_batch_group.isHidden()
    manager.show()
    qapp.processEvents()
    text_index = manager._table.model().index(0, 1)
    QTest.mouseClick(manager._table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     manager._table.visualRect(text_index).center())
    assert manager._checked_keys()

    select_index = manager._table.model().index(0, 0)
    QTest.mouseClick(manager._table.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     manager._table.visualRect(select_index).center())
    assert not manager._checked_keys()

    manager._table._check_drag_range(0, 3)
    assert len(manager._checked_keys()) == 4


def test_tray_has_explicit_auto_deploy_action(qapp):
    tray = TrayIcon()
    assert tray.action_deploy.text() == "立即重新部署"
    assert tray.action_auto_deploy.isCheckable()


def test_quick_add_system_dictionary_hint(qapp):
    class _Index:
        state = "ready"
        def ensure_ready_async(self):
            return None
        def lookup(self, text, codes):
            from src.service.system_dictionary_index import DictionaryCandidate
            return [DictionaryCandidate(text, "ce", "rime_frost", 12, 1.2)]

    dialog = QuickAddDialog(prefill_text="测试", system_dictionary_index=_Index())
    assert not dialog._dictionary_hint_box.isHidden()
    assert "原始权重 12" in dialog._dictionary_hint.text()
    assert not dialog._btn_apply_dictionary_weight.isHidden()
