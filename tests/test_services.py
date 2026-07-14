"""数据层服务测试：备份轮转 / PinyinService 默认全拼 / FieldMap 推断 / 编码生成策略。

运行：pytest tests/  （conftest 已设置 QT_QPA_PLATFORM=offscreen）
涉及拼音的用例在 pypinyin 未安装时自动跳过，不影响其余用例。
"""
from __future__ import annotations

import pytest

from src.config.field_map import FieldMap
from src.service.backup_service import BackupService

# —— pypinyin 可用性保护 —— #
try:
    import pypinyin  # noqa: F401

    _PINYIN_OK = True
except Exception:  # pragma: no cover - 依赖未装
    _PINYIN_OK = False

skip_pinyin = pytest.mark.skipif(not _PINYIN_OK, reason="pypinyin 未安装")


# ====================================================================== #
# 备份轮转
# ====================================================================== #
def test_backup_missing_source_returns_none(temp_rime_dir) -> None:
    """源文件不存在（首次创建）→ backup 返回 None，且不报错。"""
    svc = BackupService(str(temp_rime_dir), keep=3)
    assert svc.backup("not_exist.txt") is None


def test_backup_rotation_keeps_recent(temp_rime_dir) -> None:
    """反复写并备份，超过 keep 份后仅保留最近 keep 份。"""
    target = temp_rime_dir / "custom_phrase.txt"
    target.write_text("a\tb\t1\n", encoding="utf-8")
    svc = BackupService(str(temp_rime_dir), keep=3)

    for i in range(7):
        target.write_text(f"v{i}\n", encoding="utf-8")
        svc.backup("custom_phrase.txt")

    backups = svc.list_backups("custom_phrase.txt")
    assert len(backups) == 3
    # 最新一份应保留（按修改时间倒序，[0] 为最新）
    assert backups[0].stat().st_mtime >= backups[-1].stat().st_mtime


def test_backup_creates_dot_backup_dir(temp_rime_dir) -> None:
    """备份落点应为 <rime_dir>/.backup/。"""
    target = temp_rime_dir / "custom_phrase.txt"
    target.write_text("x\t y\t1\n", encoding="utf-8")
    svc = BackupService(str(temp_rime_dir), keep=5)
    dest = svc.backup("custom_phrase.txt")
    assert dest is not None
    assert dest.parent.name == ".backup"
    assert ".bak" in dest.name


def test_backup_restore_roundtrip(temp_rime_dir) -> None:
    """备份后修改源文件，再从备份恢复，内容应一致。"""
    target = temp_rime_dir / "custom_phrase.txt"
    target.write_text("原始内容\n", encoding="utf-8")
    svc = BackupService(str(temp_rime_dir), keep=5)
    dest = svc.backup("custom_phrase.txt")
    assert dest is not None

    target.write_text("已篡改\n", encoding="utf-8")
    assert target.read_text(encoding="utf-8") != "原始内容\n"

    svc.restore(str(dest), "custom_phrase.txt")
    assert target.read_text(encoding="utf-8") == "原始内容\n"


# ====================================================================== #
# PinyinService 默认全拼
# ====================================================================== #
@skip_pinyin
def test_pinyin_full_pinyin() -> None:
    """无声调全拼：银行 -> yinhang。"""
    from src.service.pinyin_service import PinyinService

    svc = PinyinService()
    assert svc.available is True
    assert svc.get_full_pinyin("银行") == "yinhang"


# ====================================================================== #
# FieldMap.infer_from_yaml
# ====================================================================== #
def test_fieldmap_infer_schema_switches_and_custom_phrase() -> None:
    """从真实结构推断：switches 名称 + custom_phrase 是否启用。"""
    data = {
        "switches": [
            {"name": "ascii_mode", "states": ["中", "Ａ"]},
            {"name": "full_shape", "states": ["半角", "全角"]},
        ],
        "engine": {
            "translators": ["script_translator", "table_translator@custom_phrase"]
        },
    }
    fm = FieldMap.default_for("schema")
    fm.infer_from_yaml(data)
    assert fm.switch_names == ["ascii_mode", "full_shape"]
    assert fm.custom_phrase_enabled is True


def test_fieldmap_infer_schema_custom_phrase_disabled() -> None:
    """translators 不含 custom_phrase 引用 → 推断为未启用。"""
    data = {
        "switches": [{"name": "ascii_mode"}],
        "engine": {"translators": ["script_translator"]},
    }
    fm = FieldMap.default_for("schema")
    fm.infer_from_yaml(data)
    assert fm.custom_phrase_enabled is False


def test_fieldmap_infer_symbols_categories() -> None:
    """从 symbols 字典键推断分类列表。"""
    data = {"symbols": {"/fh": ["a", "b"], "/dn": ["c"], "/xq": ["d"]}}
    fm = FieldMap.default_for("symbols")
    fm.infer_from_yaml(data)
    assert set(fm.categories) == {"/fh", "/dn", "/xq"}


def test_fieldmap_default_loaded_from_json() -> None:
    """默认 schema FieldMap 应含白霜标准开关（来自 default_field_map.json）。"""
    fm = FieldMap.default_for("schema")
    assert "traditionalization" in fm.switch_names
    assert "search_single_char" in fm.switch_names
    assert fm.custom_phrase_ref == "table_translator@custom_phrase"


def test_update_version_comparison_is_numeric() -> None:
    from src.service.update_service import _version_tuple

    assert _version_tuple("v1.10.0") > _version_tuple("1.9.9")
