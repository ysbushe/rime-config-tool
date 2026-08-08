import os
from pathlib import Path

import pytest

from src.service.system_dictionary_index import SystemDictionaryIndex
from src.service.pinyin_service import PinyinService


def test_system_dictionary_index_imports_and_queries(tmp_path, monkeypatch):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.dict.yaml").write_text(
        "---\nname: rime_frost\nimport_tables:\n  - base\n...\n本词\tben\t7\n", encoding="utf-8")
    (rime / "base.dict.yaml").write_text(
        "---\nname: base\n...\n系统词\txitongci\t18\n", encoding="utf-8")
    (rime / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - dicts/zi\n...\n", encoding="utf-8")
    (rime / "dicts").mkdir()
    (rime / "dicts" / "zi.dict.yaml").write_text(
        "---\nname: zi\n...\n了\tle\t1000\n还\thái\t900\n物体\twù tǐ\t800\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.service.system_dictionary_index.user_config_dir", lambda: tmp_path)

    index = SystemDictionaryIndex(str(rime))
    index.rebuild_sync()

    found = index.lookup("系统词", ["xi'tong'ci", "other"])
    assert index.state == "ready"
    assert len(found) == 1
    assert found[0].source == "rime_frost"
    assert found[0].weight == 18
    wanxiang = index.lookup_text("了")
    assert len(wanxiang) == 1
    assert wanxiang[0].code == "le"
    assert wanxiang[0].source == "wanxiang"
    assert index.lookup_text("物体")[0].code == "wuti"
    pinyin = PinyinService()
    assert [item.text for item in index.lookup_generated_code(
        "h", pinyin.split_pinyin_code
    )] == ["还"]
    assert [item.text for item in index.lookup_generated_code(
        "wut", pinyin.split_pinyin_code
    )] == ["物体"]
    assert [item.text for item in index.lookup_generated_code(
        "wti", pinyin.split_pinyin_code
    )] == ["物体"]
    assert index.lookup_generated_code("sm", pinyin.split_pinyin_code) == []


def test_system_dictionary_index_is_nonblocking_when_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr("src.service.system_dictionary_index.user_config_dir", lambda: tmp_path)
    index = SystemDictionaryIndex("")
    assert index.lookup("词", ["ci"]) == []


@pytest.mark.skipif(
    os.environ.get("RIME_CONFIG_RUN_DICTIONARY_BACKUP_TESTS") != "1",
    reason="full local dictionary snapshot integration test is opt-in",
)
def test_local_dictionary_backup_finds_wanxiang_character(tmp_path, monkeypatch):
    backup_rime = (
        Path(__file__).resolve().parents[1]
        / "backups" / "rime-test-dictionaries-20260807" / "Rime"
    )
    assert (backup_rime / "wanxiang.dict.yaml").is_file()
    monkeypatch.setattr(
        "src.service.system_dictionary_index.user_config_dir", lambda: tmp_path
    )
    index = SystemDictionaryIndex(str(backup_rime))

    index.rebuild_sync()

    matches = index.lookup_text("了")
    assert any(item.code.startswith("le") for item in matches)
    assert any(item.source == "wanxiang" for item in matches)
    pinyin = PinyinService()
    assert any(item.text == "还" for item in index.lookup_generated_code(
        "h", pinyin.split_pinyin_code, limit=20
    ))
    assert any(item.text == "物体" for item in index.lookup_generated_code(
        "wut", pinyin.split_pinyin_code, limit=100
    ))
    assert any(item.text == "物体" for item in index.lookup_generated_code(
        "wti", pinyin.split_pinyin_code, limit=100
    ))
