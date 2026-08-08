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
    client = DataEngineClient(timeout_seconds=5)
    index = VNextDictionaryIndex(str(rime), client, tmp_path / "indexes")
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
    finally:
        client.close()


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
