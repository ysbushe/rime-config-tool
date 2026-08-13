import threading
import time

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    CompactDictionarySource,
)
from src.service.data_engine_service import DataEngineClient
from src.service.pinyin_service import PinyinService


def _build_index(tmp_path):
    source = tmp_path / "base.dict.yaml"
    source.write_text(
        "---\nname: base\n...\n还\thái\t900\n物体\twù tǐ\t800\n",
        encoding="utf-8",
    )
    path = tmp_path / "wanxiang.sqlite"
    CompactDictionaryIndex(path, "wanxiang").build(
        [CompactDictionarySource(source, "wanxiang", 1.2)],
        PinyinService().split_pinyin_code,
    )
    return path


def test_data_engine_protocol_and_lookup(tmp_path):
    path = _build_index(tmp_path)
    client = DataEngineClient(timeout_seconds=5)
    try:
        assert client.request("hello")["protocol_version"] == 1
        result = client.request("lookup_code", {
            "index_path": str(path), "scheme_id": "wanxiang",
            "query": "wti", "limit": 20,
        })
        assert [row["text"] for row in result["candidates"]] == ["物体"]
    finally:
        client.close()


def test_data_engine_reads_dictionary_page_from_legacy_compact_index(tmp_path):
    path = _build_index(tmp_path)
    source = tmp_path / "base.dict.yaml"
    source.unlink()
    client = DataEngineClient(timeout_seconds=5)
    try:
        page = client.request("indexed_dictionary_entries_page", {
            "index_path": str(path), "scheme_id": "wanxiang",
            "paths": [str(source)], "query": "", "limit": 20,
        })
        assert [item["text"] for item in page["entries"]] == ["还", "物体"]
    finally:
        client.close()


def test_data_engine_latest_request_suppresses_stale_callback(tmp_path):
    path = _build_index(tmp_path)
    client = DataEngineClient(timeout_seconds=5)
    finished = threading.Event()
    received = []
    payload = {"index_path": str(path), "scheme_id": "wanxiang", "limit": 20}
    try:
        client.request_latest(
            "lookup_code", {**payload, "query": "h"},
            lambda result, error: received.append((result, error, "old")),
        )
        client.request_latest(
            "lookup_code", {**payload, "query": "wut"},
            lambda result, error: (received.append((result, error, "new")), finished.set()),
        )
        assert finished.wait(5)
        assert [item[2] for item in received] == ["new"]
        assert received[0][0]["candidates"][0]["text"] == "物体"
        assert received[0][1] is None
    finally:
        client.close()


def test_data_engine_builds_scheme_scoped_dictionary_snapshot(tmp_path):
    (tmp_path / "default.custom.yaml").write_text(
        "patch:\n  schema_list:\n    - schema: wanxiang\n", encoding="utf-8",
    )
    (tmp_path / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - dicts/base\n...\n",
        encoding="utf-8",
    )
    (tmp_path / "dicts").mkdir()
    (tmp_path / "dicts" / "base.dict.yaml").write_text(
        "---\nname: base\n...\n测试\tce shi\t1\n", encoding="utf-8",
    )
    client = DataEngineClient(timeout_seconds=5)
    try:
        result = client.request("dictionary_snapshot", {"rime_dir": str(tmp_path)})
        assert result["schemes"] == ["wanxiang"]
        assert result["catalogs"]["wanxiang"]["total"] == 2
    finally:
        client.close()


def test_data_engine_background_refresh_and_paged_lookup(tmp_path):
    (tmp_path / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - base\n...\n",
        encoding="utf-8",
    )
    (tmp_path / "base.dict.yaml").write_text(
        "---\nname: base\n...\n" + "".join(
            f"候选{index}\th\t{100 - index}\n" for index in range(5)
        ),
        encoding="utf-8",
    )
    index_path = tmp_path / "indexes" / "wanxiang.sqlite"
    client = DataEngineClient(timeout_seconds=5)
    try:
        started = client.request("start_index_refresh", {
            "index_path": str(index_path),
            "scheme_id": "wanxiang",
            "rime_dir": str(tmp_path),
            "roots": [["wanxiang", 1.2]],
        })
        task_id = started["task_id"]
        deadline = time.monotonic() + 5
        while True:
            status = client.request("index_task_status", {"task_id": task_id})
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["status"] == "completed", status
        assert status["report"]["entry_count"] == 5

        first = client.request("lookup_code_page", {
            "index_path": str(index_path), "scheme_id": "wanxiang",
            "query": "h", "limit": 2,
        })
        second = client.request("lookup_code_page", {
            "index_path": str(index_path), "scheme_id": "wanxiang",
            "query": "h", "limit": 2, "cursor": first["next_cursor"],
        })
        assert [row["text"] for row in first["candidates"]] == ["候选0", "候选1"]
        assert [row["text"] for row in second["candidates"]] == ["候选2", "候选3"]
    finally:
        client.close()


def test_data_engine_sharded_refresh_query_and_dictionary_page(tmp_path):
    (tmp_path / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - base\n...\n",
        encoding="utf-8",
    )
    source = tmp_path / "base.dict.yaml"
    source.write_text(
        "---\nname: base\n...\n还\thái\t900\n物体\twù tǐ\t800\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "indexes" / "v3" / "wanxiang"
    maintenance = DataEngineClient(timeout_seconds=5)
    query = DataEngineClient(timeout_seconds=5)
    try:
        started = maintenance.request("start_sharded_index_refresh", {
            "index_dir": str(index_dir),
            "scheme_id": "wanxiang",
            "rime_dir": str(tmp_path),
            "roots": [["wanxiang", 1.2]],
        })
        deadline = time.monotonic() + 5
        while True:
            status = maintenance.request(
                "index_task_status", {"task_id": started["task_id"]},
            )
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["status"] == "completed", status

        found = query.request("sharded_lookup_code", {
            "index_dir": str(index_dir), "scheme_id": "wanxiang",
            "query": "wti", "limit": 20,
        })
        page = query.request("sharded_dictionary_entries_page", {
            "index_dir": str(index_dir), "scheme_id": "wanxiang",
            "paths": [str(source)], "query": "", "limit": 20,
        })
        assert [item["text"] for item in found["candidates"]] == ["物体"]
        assert [item["text"] for item in page["entries"]] == ["还", "物体"]
    finally:
        query.close()
        maintenance.close()


def test_data_engine_dictionary_file_pages_and_exact_matching(tmp_path):
    source = tmp_path / "dicts" / "base.dict.yaml"
    source.parent.mkdir()
    source.write_text(
        "---\nname: base\n...\n" + "".join(
            f"候选{index}\thx{index}\t{100 - index}\n" for index in range(5)
        ),
        encoding="utf-8",
    )
    client = DataEngineClient(timeout_seconds=5)
    try:
        first = client.request("dictionary_entries_page", {
            "rime_dir": str(tmp_path), "paths": [str(source)], "limit": 2,
        })
        second = client.request("dictionary_entries_page", {
            "rime_dir": str(tmp_path), "paths": [str(source)], "limit": 2,
            "cursor": first["next_cursor"],
        })
        exact = client.request("dictionary_entries_page", {
            "rime_dir": str(tmp_path), "paths": [str(source)],
            "query": "候选1", "limit": 20,
        })
        partial = client.request("dictionary_entries_page", {
            "rime_dir": str(tmp_path), "paths": [str(source)],
            "query": "候选", "limit": 20,
        })

        assert [row["text"] for row in first["entries"]] == ["候选0", "候选1"]
        assert [row["text"] for row in second["entries"]] == ["候选2", "候选3"]
        assert [row["text"] for row in exact["entries"]] == ["候选1"]
        assert partial["entries"] == []
    finally:
        client.close()


def test_latest_request_channels_do_not_cancel_each_other(tmp_path):
    path = _build_index(tmp_path)
    client = DataEngineClient(timeout_seconds=5)
    finished = threading.Event()
    received = []

    def deliver(label):
        def callback(result, error):
            received.append((label, result, error))
            if len(received) == 2:
                finished.set()
        return callback

    try:
        payload = {"index_path": str(path), "scheme_id": "wanxiang", "limit": 20}
        client.request_latest(
            "lookup_code", {**payload, "query": "h"}, deliver("quick-tune"),
            channel="quick-tune",
        )
        client.request_latest(
            "lookup_code", {**payload, "query": "wut"}, deliver("settings"),
            channel="settings",
        )
        assert finished.wait(5)
        assert {item[0] for item in received} == {"quick-tune", "settings"}
        assert all(item[2] is None for item in received)
    finally:
        client.close()


def test_latest_request_channel_has_bounded_backlog(monkeypatch):
    client = DataEngineClient(timeout_seconds=5)
    started = threading.Event()
    release = threading.Event()
    delivered = threading.Event()
    calls = []

    def request(_operation, payload=None):
        calls.append(int(payload["value"]))
        if len(calls) == 1:
            started.set()
            release.wait(2)
        return {"value": payload["value"]}

    monkeypatch.setattr(client, "request", request)
    try:
        client.request_latest("test", {"value": 0}, lambda *_: None, channel="typing")
        assert started.wait(1)
        received = []
        for value in range(1, 20):
            client.request_latest(
                "test", {"value": value},
                lambda result, error: (
                    received.append((result, error)), delivered.set()
                ),
                channel="typing",
            )
        release.set()
        assert delivered.wait(2)
        assert calls == [0, 19]
        assert received == [({"value": 19}, None)]
    finally:
        client.close()
