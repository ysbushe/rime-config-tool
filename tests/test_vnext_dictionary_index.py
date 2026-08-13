from __future__ import annotations

import time
from unittest.mock import MagicMock

from src.service.data_engine_service import DataEngineClient
from src.service.vnext_dictionary_index import VNextDictionaryIndex


def test_vnext_index_builds_only_active_scheme_and_resolves_shorthand(tmp_path):
    rime = tmp_path / "Rime"
    (rime / "dicts").mkdir(parents=True)
    (rime / "default.custom.yaml").write_text(
        "patch:\n  schema_list:\n    - schema: wanxiang\n", encoding="utf-8",
    )
    (rime / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - dicts/base\n...\n",
        encoding="utf-8",
    )
    (rime / "dicts" / "base.dict.yaml").write_text(
        "---\nname: base\n...\n还\thái\t900\n物体\twù tǐ\t800\n",
        encoding="utf-8",
    )
    (rime / "rime_frost.dict.yaml").write_text(
        "---\nname: frost\n...\n雾凇\twu song\t999\n", encoding="utf-8",
    )
    query_client = DataEngineClient(timeout_seconds=5)
    maintenance_client = DataEngineClient(timeout_seconds=5)
    index = VNextDictionaryIndex(
        str(rime), query_client, tmp_path / "indexes", maintenance_client,
    )
    try:
        index.ensure_ready_async()
        deadline = time.monotonic() + 5
        while index.state == "building" and time.monotonic() < deadline:
            time.sleep(0.02)

        assert index.state == "ready"
        assert index.scheme_id == "wanxiang"
        assert [item.text for item in index.lookup_code("h")] == ["还"]
        assert [item.text for item in index.lookup_code("wut")] == ["物体"]
        assert index.lookup_text("雾凇") == []
        assert index._storage_kind == "sharded"
        assert index._index_path.name == "catalog.sqlite"
        assert maintenance_client._process is None
    finally:
        query_client.close()
        maintenance_client.close()


def test_vnext_cancel_build_is_nonblocking_and_changes_state(tmp_path):
    client = MagicMock()
    index = VNextDictionaryIndex(str(tmp_path), client, tmp_path / "indexes")
    index._state = "building"
    index._task_id = "task-1"

    assert index.cancel_build() is True
    deadline = time.monotonic() + 1
    while not client.request.called and time.monotonic() < deadline:
        time.sleep(0.01)

    assert index.state == "cancelling"
    client.request.assert_called_with("cancel_index_task", {"task_id": "task-1"})


def test_ready_vnext_index_is_not_refreshed_without_force(tmp_path, monkeypatch):
    client = MagicMock()
    index = VNextDictionaryIndex(str(tmp_path), client, tmp_path / "indexes")
    index_path = tmp_path / "indexes" / "wanxiang.sqlite"
    index_path.parent.mkdir()
    index_path.touch()
    index._state = "ready"
    index._scheme_id = "wanxiang"
    index._index_path = index_path
    worker = MagicMock()
    monkeypatch.setattr(index, "_ensure_worker", worker)

    index.ensure_ready_async()
    worker.assert_not_called()

    index.ensure_ready_async(force=True)
    assert index._thread is not None
    index._thread.join(timeout=1)
    worker.assert_called_once()


def test_vnext_attaches_existing_sharded_generation_before_refresh(tmp_path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "default.custom.yaml").write_text(
        "patch:\n  schema_list:\n    - schema: wanxiang\n", encoding="utf-8",
    )
    source = rime / "wanxiang.dict.yaml"
    source.write_text(
        "---\nname: wanxiang\n...\n测试\tce shi\t1\n", encoding="utf-8",
    )
    from src.service.compact_dictionary_index import CompactDictionarySource
    from src.service.sharded_dictionary_index import ShardedDictionaryIndex
    shard_index = ShardedDictionaryIndex(
        tmp_path / "indexes" / "v3" / "wanxiang", "wanxiang",
    )
    shard_index.refresh([CompactDictionarySource(source, "wanxiang")])
    catalog = shard_index.catalog_path

    index = VNextDictionaryIndex(str(rime), MagicMock(), tmp_path / "indexes")

    assert index.state == "ready"
    assert index.scheme_id == "wanxiang"
    assert index._storage_kind == "sharded"
    assert index._index_path == catalog


def test_vnext_queries_last_generation_through_client_separate_from_maintenance(
        tmp_path) -> None:
    query = MagicMock()
    query.request.return_value = {
        "candidates": [{
            "text": "旧词", "code": "jiuci", "source": "base",
            "weight": 1, "quality": 1.0,
        }],
    }
    maintenance = MagicMock()
    index = VNextDictionaryIndex(
        str(tmp_path), query, tmp_path / "indexes", maintenance,
    )
    catalog = tmp_path / "indexes" / "v3" / "wanxiang" / "catalog.sqlite"
    catalog.parent.mkdir(parents=True)
    catalog.touch()
    index._scheme_id = "wanxiang"
    index._index_path = catalog
    index._storage_kind = "sharded"
    index._state = "building"

    result = index.lookup_code("jc")

    assert [item.text for item in result] == ["旧词"]
    query.request.assert_called_once()
    maintenance.request.assert_not_called()


def test_vnext_dictionary_page_uses_legacy_compact_index_during_migration(tmp_path):
    client = MagicMock()
    client.request_latest.return_value = 7
    index = VNextDictionaryIndex(str(tmp_path), client, tmp_path / "indexes")
    legacy = tmp_path / "indexes" / "wanxiang.sqlite"
    legacy.parent.mkdir()
    legacy.touch()
    source = tmp_path / "dicts" / "base.dict.yaml"
    index._scheme_id = "wanxiang"
    index._index_path = legacy
    index._storage_kind = "compact"

    generation = index.request_entries_page_latest(
        [source], "测试", 200, None, MagicMock(), channel="dictionary-center",
    )

    assert generation == 7
    operation, payload, _callback = client.request_latest.call_args.args
    assert operation == "indexed_dictionary_entries_page"
    assert payload["paths"] == [str(source.resolve())]
