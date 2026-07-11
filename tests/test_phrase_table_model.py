"""PhraseTableModel 单元测试（基于临时 Rime 副本，不触碰真实配置）。

重点覆盖 Bug A 修复：data() 需同时处理 DisplayRole 与 EditRole，
使双击内联编辑器打开时预填当前内容（否则编辑器空白、回车会清空词条）。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt, QModelIndex

from src.repo.phrase_repo import PhraseRepo
from src.ui.phrase_table_model import (
    PhraseTableModel,
    COL_TEXT,
    COL_CODE,
    COL_WEIGHT,
    COL_GROUP,
    COL_SELECT,
)

UNGROUPED = "未分组"


def _make_model(phrase_repo: PhraseRepo, group_of=None):
    phrases = phrase_repo.all()
    model = PhraseTableModel()
    model.set_phrases(phrases, group_of=group_of)
    return model


def test_edit_role_matches_display_role(qapp, phrase_repo):
    """Bug A：COL_TEXT/COL_CODE/COL_WEIGHT/COL_GROUP 的 EditRole
    必须与 DisplayRole 返回相同内容。"""
    model = _make_model(phrase_repo)

    def get(row, col, role):
        return model.data(model.index(row, col), role)

    for row in range(model.rowCount()):
        for col in (COL_TEXT, COL_CODE, COL_WEIGHT, COL_GROUP):
            disp = get(row, col, Qt.ItemDataRole.DisplayRole)
            edit = get(row, col, Qt.ItemDataRole.EditRole)
            assert edit is not None, f"row={row} col={col} EditRole 不应为 None"
            assert edit == disp, f"row={row} col={col} EditRole 须等于 DisplayRole"


def test_text_edit_role_is_actual_text(qapp, phrase_repo):
    """编辑器初值应等于词条真实文本（而非空串）。"""
    model = _make_model(phrase_repo)
    p = phrase_repo.all()[0]
    edit_text = model.data(model.index(0, COL_TEXT), Qt.ItemDataRole.EditRole)
    assert edit_text == p.text
    assert edit_text != ""


def test_group_edit_role_unknown_unless_callback(qapp, phrase_repo):
    """未提供 group_of 时，分组列 EditRole 返回未分组占位。"""
    model = _make_model(phrase_repo)
    edit_group = model.data(model.index(0, COL_GROUP), Qt.ItemDataRole.EditRole)
    assert edit_group == UNGROUPED


def test_group_edit_role_with_callback(qapp, phrase_repo):
    """提供 group_of 时，分组列 EditRole 返回回调结果。"""
    model = _make_model(phrase_repo, group_of=lambda p: "我的分组")
    edit_group = model.data(model.index(0, COL_GROUP), Qt.ItemDataRole.EditRole)
    assert edit_group == "我的分组"


def test_select_column_has_no_edit_role(qapp, phrase_repo):
    """选择列是 CheckStateRole，不应提供 EditRole（保持原行为）。"""
    model = _make_model(phrase_repo)
    assert model.data(model.index(0, COL_SELECT), Qt.ItemDataRole.EditRole) is None
