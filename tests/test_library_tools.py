from pathlib import Path

from src.repo.phrase_repo import Phrase
from src.service.library_tools import (
    DuplicateIndex,
    MetadataStore,
    diff_phrase_lines,
    export_text,
    health_check,
    parse_import_text,
)


def test_duplicate_index_finds_text_and_code_conflicts():
    phrases = [
        Phrase("测试", "ce'shi", 1),
        Phrase("测试", "c's", 2),
        Phrase("甲", "jia", 1),
        Phrase("乙", "jia", 1),
    ]
    index = DuplicateIndex.build(phrases)
    assert set(index.by_text) == {"测试"}
    assert set(index.by_code) == {"jia"}


def test_metadata_is_local_and_roundtrips(monkeypatch, tmp_path):
    import src.service.library_tools as tools
    monkeypatch.setattr(tools, "user_config_dir", lambda: tmp_path / "local")
    store = MetadataStore(tmp_path / "rime")
    store.set_entry("测试", "ce'shi", "备注", ["工作", "工作"])
    store.record("新增", "测试")
    store.save()
    assert "rime" not in str(store.path.parent)
    reloaded = MetadataStore(tmp_path / "rime")
    assert reloaded.entry("测试", "ce'shi") == {"note": "备注", "tags": ["工作"]}
    assert reloaded.history()[0]["action"] == "新增"


def test_import_export_health_and_diff():
    entries, errors = parse_import_text("甲\tjia\t2\n坏行\n乙,yi,101")
    assert errors == ["第 2 行格式无效"]
    assert [(item.text, item.code, item.weight) for item in entries] == [("甲", "jia", 2), ("乙", "yi", 99)]
    assert "甲\tjia\t2" in export_text(entries)
    assert diff_phrase_lines("甲\tjia\t1\n", "乙\tyi\t1\n") == {
        "新增": ["甲\tjia\t1"], "删除": ["乙\tyi\t1"], "未变": []
    }
    issues = health_check([Phrase("", "", 100), Phrase("", "", 1)])
    assert {item.kind for item in issues} >= {"empty_text", "empty_code", "weight", "exact_duplicate"}
