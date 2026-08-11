from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QBoxLayout, QScrollArea, QTabWidget

from src.service.backup_service import BackupService
from src.service.file_transaction import TransactionalFileWriter
from src.service.rime_patch_service import RimePatchService
from src.ui.configuration_pages import CandidateAppearanceWidget, InputHabitsWidget


def _providers(tmp_path, schema_id: str = "wanxiang"):
    service = RimePatchService(
        str(tmp_path), BackupService(tmp_path, 3, tmp_path / "backups"),
        TransactionalFileWriter(tmp_path / "transactions"),
    )
    files = SimpleNamespace(display_name="万象拼音" if schema_id == "wanxiang" else "白霜拼音")
    resolution = SimpleNamespace(
        schema_id=schema_id, files=files, writable=True, reason="test",
    )
    return lambda: service, lambda: resolution


def test_candidate_preview_stays_outside_settings_scroll(qapp, tmp_path) -> None:
    service, environment = _providers(tmp_path)
    page = CandidateAppearanceWidget(service, environment)
    page.activate()

    scroll = page.findChild(QScrollArea, "CandidateSettingsScroll")
    preview = page._preview_surface

    assert scroll is not None
    assert not scroll.isAncestorOf(preview)
    assert page._font.isEditable()
    assert page._font_point.isEditable()
    assert page._candidate_spacing.isEditable()
    assert page._corner.isEditable()


def test_candidate_preview_distinguishes_all_layout_modes(qapp, tmp_path) -> None:
    service, environment = _providers(tmp_path)
    page = CandidateAppearanceWidget(service, environment)
    page.activate()

    page._layout.setCurrentIndex(page._layout.findData("vertical"))
    assert page._preview_row.direction() == QBoxLayout.Direction.TopToBottom
    assert page._preview_labels[0].text() == "1 你好"
    assert "文字横着读" in page._preview_mode.text()

    page._layout.setCurrentIndex(page._layout.findData("vertical_text"))
    assert page._preview_row.direction() == QBoxLayout.Direction.RightToLeft
    assert page._preview_labels[0].text() == "1\n你\n好"

    page._layout.setCurrentIndex(page._layout.findData("horizontal+fullscreen"))
    assert page._preview_row.direction() == QBoxLayout.Direction.LeftToRight
    assert not page._preview_fullscreen.isHidden()
    assert "全屏候选窗" in page._preview_mode.text()

    page._layout.setCurrentIndex(page._layout.findData("vertical+fullscreen"))
    assert page._preview_row.direction() == QBoxLayout.Direction.TopToBottom
    assert not page._preview_fullscreen.isHidden()

    for _ in range(10):
        page._refresh_preview()
    assert len(page._preview_labels) == 4


def test_candidate_preview_explains_and_renders_preedit_modes(qapp, tmp_path) -> None:
    service, environment = _providers(tmp_path)
    page = CandidateAppearanceWidget(service, environment)
    page.activate()

    page._inline.setChecked(False)
    page._preedit.setCurrentIndex(page._preedit.findData("preview"))
    assert not page._preview_preedit.isHidden()
    assert "你好" in page._preview_preedit.text()
    assert "当前高亮" in page._preedit_help.text()

    page._inline.setChecked(True)
    page._preedit.setCurrentIndex(page._preedit.findData("preview_all"))
    assert page._preview_preedit.isHidden()
    assert "欢迎使用" in page._preview_input.text()
    assert "整段输入" in page._preedit_help.text()


def test_skin_editor_exposes_library_four_editor_tabs_and_full_colors(qapp, tmp_path) -> None:
    (tmp_path / "weasel.yaml").write_text(
        "preset_color_schemes:\n  aqua:\n    name: Aqua\n",
        encoding="utf-8",
    )
    service, environment = _providers(tmp_path)
    page = CandidateAppearanceWidget(service, environment)
    page.activate()

    assert page._sections.count() == 3
    assert [page._skin_tabs.tabText(index) for index in range(page._skin_tabs.count())] == [
        "基础", "字体", "布局", "配色",
    ]
    assert len(page._color_rows) == 22
    assert page._skin_list.count() == 1
    assert "只读预设" in page._status.text()


def test_weasel_settings_use_supported_interaction_and_app_option_keys(qapp, tmp_path) -> None:
    service, environment = _providers(tmp_path)
    page = CandidateAppearanceWidget(service, environment)
    page.activate()
    page._hover.setCurrentIndex(page._hover.findData("semi_hilite"))
    page._ascii_cursor.setChecked(True)
    page._app_exe.setText("Code.EXE")
    page._app_ascii.setChecked(True)
    page._app_inline.setChecked(True)
    page._update_app_rule()

    updates = page._weasel_setting_updates()

    assert updates["style/hover_type"] == "semi_hilite"
    assert updates["style/ascii_tip_follow_cursor"] is True
    assert updates["app_options"]["code.exe"] == {
        "ascii_mode": True, "inline_preedit": True, "vim_mode": False,
    }
    assert {"schema/full_icon", "schema/half_icon"} <= set(page._icon_edits)


def test_input_habits_supports_multiple_paging_pairs_without_ctrl(qapp, tmp_path) -> None:
    service, environment = _providers(tmp_path)
    page = InputHabitsWidget(service, environment)
    page.activate()
    page._paging_brackets.setChecked(True)
    page._paging_comma.setChecked(True)
    page._select_extra.setChecked(True)

    updates = page._updates()
    accepts = {item["accept"] for item in updates["key_binder/bindings/+"]}

    assert {"bracketleft", "bracketright", "comma", "period"} <= accepts
    assert {"semicolon", "apostrophe"} <= accepts
    assert not any("Control" in value or "Ctrl" in value for value in accepts)
    assert page.filename == "wanxiang.custom.yaml"
    assert "wanxiang_english/english_spacing" in updates
