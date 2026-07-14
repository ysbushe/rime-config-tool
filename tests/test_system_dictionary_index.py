from pathlib import Path

from src.service.system_dictionary_index import SystemDictionaryIndex


def test_system_dictionary_index_imports_and_queries(tmp_path, monkeypatch):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.dict.yaml").write_text(
        "---\nname: rime_frost\nimport_tables:\n  - base\n...\n本词\tben\t7\n", encoding="utf-8")
    (rime / "base.dict.yaml").write_text(
        "---\nname: base\n...\n系统词\txitongci\t18\n", encoding="utf-8")
    monkeypatch.setattr("src.service.system_dictionary_index.user_config_dir", lambda: tmp_path)

    index = SystemDictionaryIndex(str(rime))
    index.rebuild_sync()

    found = index.lookup("系统词", ["xi'tong'ci", "other"])
    assert index.state == "ready"
    assert len(found) == 1
    assert found[0].source == "rime_frost"
    assert found[0].weight == 18


def test_system_dictionary_index_is_nonblocking_when_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr("src.service.system_dictionary_index.user_config_dir", lambda: tmp_path)
    index = SystemDictionaryIndex("")
    assert index.lookup("词", ["ci"]) == []
