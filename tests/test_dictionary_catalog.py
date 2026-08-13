from __future__ import annotations

from unittest.mock import MagicMock

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    CompactDictionarySource,
)
from src.service.data_engine_service import DataEngineClient
from src.service.dictionary_catalog import DictionaryCatalogService
from src.service.file_transaction import TransactionalFileWriter
from src.service.vnext_dictionary_index import VNextDictionaryIndex
from src.ui.dictionary_center_dialog import DictionaryCenterDialog


def _write_dictionary(path, imports=(), rows=()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["---", f"name: {path.stem}", "version: '1'"]
    if imports:
        header.append("import_tables:")
        header.extend(f"  - {item}" for item in imports)
    header.append("...")
    body = ["\t".join(row) for row in rows]
    path.write_text("\n".join([*header, *body, ""]), encoding="utf-8")


def _activate(root, *schemas: str) -> None:
    values = "\n".join(f"    - schema: {item}" for item in schemas)
    (root / "default.custom.yaml").write_text(
        f"patch:\n  schema_list:\n{values}\n", encoding="utf-8"
    )


def test_catalog_isolates_wanxiang_from_frost(tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(
        tmp_path / "wanxiang.dict.yaml", ("dicts/zi",),
    )
    _write_dictionary(
        tmp_path / "dicts/zi.dict.yaml", rows=(("了", "le", "100"),)
    )
    _write_dictionary(tmp_path / "rime_frost.dict.yaml")
    _write_dictionary(tmp_path / "cn_dicts/base.dict.yaml")

    service = DictionaryCatalogService(tmp_path)
    paths = {item.relative_path for item in service.catalog("wanxiang")}

    assert "wanxiang.dict.yaml" in paths
    assert "dicts/zi.dict.yaml" in paths
    assert "rime_frost.dict.yaml" not in paths
    assert "cn_dicts/base.dict.yaml" not in paths


def test_catalog_isolates_frost_from_wanxiang(tmp_path) -> None:
    _activate(tmp_path, "rime_frost")
    _write_dictionary(
        tmp_path / "rime_frost.dict.yaml", ("cn_dicts/base",),
    )
    _write_dictionary(tmp_path / "cn_dicts/base.dict.yaml")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml")

    paths = {
        item.relative_path
        for item in DictionaryCatalogService(tmp_path).catalog("rime_frost")
    }

    assert "rime_frost.dict.yaml" in paths
    assert "cn_dicts/base.dict.yaml" in paths
    assert "wanxiang.dict.yaml" not in paths


def test_catalog_preserves_filename_purpose_and_entry_source(tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(
        tmp_path / "wanxiang.dict.yaml", ("dicts/zi",),
    )
    _write_dictionary(
        tmp_path / "dicts/zi.dict.yaml",
        rows=(("物体", "wùtǐ", "34672"), ("无题", "wuti", "10")),
    )
    service = DictionaryCatalogService(tmp_path)
    item = next(
        item for item in service.catalog("wanxiang")
        if item.relative_path == "dicts/zi.dict.yaml"
    )

    entries = service.read_entries(item, query="物体")

    assert item.purpose == "单字及带调辅助码映射词库"
    assert [(entry.text, entry.code, entry.source_file) for entry in entries] == [
        ("物体", "wùtǐ", "dicts/zi.dict.yaml")
    ]


def test_catalog_lists_userdb_as_read_only(tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml")
    (tmp_path / "wanxiang.userdb").mkdir()

    item = next(
        item for item in DictionaryCatalogService(tmp_path).catalog("wanxiang")
        if item.relative_path == "wanxiang.userdb"
    )

    assert item.kind == "学习词库（只读）"
    assert item.entry_paths == ()
    assert item.editable is False


def test_available_schemes_preserves_multiple_active_choices(tmp_path) -> None:
    _activate(tmp_path, "wanxiang", "rime_frost")

    assert DictionaryCatalogService(tmp_path).available_schemes() == (
        "wanxiang", "rime_frost"
    )


def test_dictionary_entry_query_is_exact_not_substring(tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(
        tmp_path / "wanxiang.dict.yaml", ("dicts/base",),
    )
    _write_dictionary(
        tmp_path / "dicts/base.dict.yaml",
        rows=(("物体", "wuti", "10"), ("物体学", "wutixue", "9")),
    )
    service = DictionaryCatalogService(tmp_path)
    item = next(
        item for item in service.catalog("wanxiang")
        if item.relative_path == "dicts/base.dict.yaml"
    )

    assert [entry.text for entry in service.read_entries(item, query="物体")] == ["物体"]
    assert service.read_entries(item, query="wut") == []


def test_import_table_discovery_streams_only_header(tmp_path, monkeypatch) -> None:
    path = tmp_path / "large.dict.yaml"
    path.write_text(
        "---\nname: large\nimport_tables:\n  - dicts/base\n...\n"
        + ("正文\tzhengwen\t1\n" * 10000),
        encoding="utf-8",
    )

    def forbidden_read_text(*_args, **_kwargs):
        raise AssertionError("large dictionary must not be loaded with read_text")

    monkeypatch.setattr(type(path), "read_text", forbidden_read_text)
    assert list(DictionaryCatalogService._import_tables(path)) == ["dicts/base"]


def test_dictionary_center_edits_personal_text_dictionary_with_explicit_save(
        qapp, tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml")
    (tmp_path / "wanxiang.schema.yaml").write_text(
        "schema:\n  schema_id: wanxiang\ntranslator:\n  user_dict: wanxiang_user_phrase\n",
        encoding="utf-8",
    )
    personal = tmp_path / "wanxiang_user_phrase.txt"
    personal.write_text("# personal\n旧词\tjiuci\t1\n", encoding="utf-8")
    dialog = DictionaryCenterDialog(
        tmp_path,
        transaction_writer=TransactionalFileWriter(tmp_path / "transactions"),
    )
    row = next(
        row for row, item in enumerate(dialog._catalog)
        if item.relative_path == "wanxiang_user_phrase.txt"
    )

    dialog._catalog_table.selectRow(row)
    dialog._entry_table.item(0, 0).setText("新词")

    assert "旧词" in personal.read_text(encoding="utf-8")
    assert dialog._save_button.isEnabled()
    dialog._save_button.click()

    saved = personal.read_text(encoding="utf-8")
    assert "新词\tjiuci\t1" in saved
    assert "旧词" not in saved
    assert not dialog._save_button.isEnabled()
    dialog.done(dialog.DialogCode.Accepted)


def test_dictionary_center_keeps_staged_edit_after_external_conflict(
        qapp, tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml")
    (tmp_path / "wanxiang.schema.yaml").write_text(
        "schema:\n  schema_id: wanxiang\ntranslator:\n  user_dict: wanxiang_user_phrase\n",
        encoding="utf-8",
    )
    personal = tmp_path / "wanxiang_user_phrase.txt"
    personal.write_text("旧词\tjiuci\t1\n", encoding="utf-8")
    dialog = DictionaryCenterDialog(
        tmp_path,
        transaction_writer=TransactionalFileWriter(tmp_path / "transactions"),
    )
    row = next(
        row for row, item in enumerate(dialog._catalog)
        if item.relative_path == "wanxiang_user_phrase.txt"
    )
    dialog._catalog_table.selectRow(row)
    dialog._entry_table.item(0, 0).setText("暂存词")
    personal.write_text("外部词\twaibuci\t2\n", encoding="utf-8")

    dialog._save_button.click()

    assert personal.read_text(encoding="utf-8") == "外部词\twaibuci\t2\n"
    assert dialog._editable_dirty is True
    assert "暂存修改已保留" in dialog._status.text()


def test_embedded_dictionary_center_loads_catalog_without_blocking_gui(
        qapp, tmp_path, monkeypatch) -> None:
    import time
    from PySide6.QtTest import QTest

    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml")
    original = DictionaryCatalogService.catalog

    def slow_catalog(self, schema_id):
        time.sleep(0.15)
        return original(self, schema_id)

    monkeypatch.setattr(DictionaryCatalogService, "catalog", slow_catalog)
    started = time.perf_counter()
    dialog = DictionaryCenterDialog(tmp_path, embedded=True)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.10
    assert "后台读取" in dialog._status.text()
    # The full suite also exercises the real-size local dictionary snapshot;
    # leave enough scheduling margin for a busy Windows runner while keeping
    # the constructor latency assertion strict.
    for _ in range(250):
        QTest.qWait(20)
        if not dialog._catalog_loading:
            break
    assert dialog._catalog_loading is False
    dialog.dispose()


def test_dictionary_center_uses_ready_index_instead_of_raw_dictionary_scan(
        qapp, tmp_path) -> None:
    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml", ("dicts/base",))
    source = tmp_path / "dicts/base.dict.yaml"
    _write_dictionary(source, rows=(("测试", "ceshi", "1"),))

    class ReadyIndex:
        def __init__(self):
            self.requests = []

        def request_entries_page_latest(
                self, paths, query, limit, cursor, callback, *, channel):
            self.requests.append((tuple(paths), query, limit, cursor, channel))
            callback({
                "entries": [{
                    "text": "测试", "code": "ceshi", "weight": "1",
                    "source_file": "dicts/base.dict.yaml",
                }],
                "next_cursor": None,
            }, None)
            return 1

        def cancel_pending(self, _channel):
            return None

        def ensure_ready_async(self):
            raise AssertionError("ready index should not be rebuilt")

    index = ReadyIndex()
    raw_engine = MagicMock()
    dialog = DictionaryCenterDialog(
        tmp_path, dictionary_index=index, data_engine_client=raw_engine,
    )
    row = next(
        row for row, item in enumerate(dialog._catalog)
        if item.relative_path == "dicts/base.dict.yaml"
    )

    dialog._catalog_table.selectRow(row)
    dialog._poll_results()

    assert index.requests
    assert index.requests[0][0] == (source,)
    raw_engine.request_latest.assert_not_called()
    assert dialog._entry_table.item(0, 0).text() == "测试"
    dialog.dispose()


def test_dictionary_center_browses_existing_legacy_index_during_v3_migration(
        qapp, tmp_path) -> None:
    """The last compact generation must stay browseable while v3 is absent."""
    from PySide6.QtTest import QTest

    _activate(tmp_path, "wanxiang")
    _write_dictionary(tmp_path / "wanxiang.dict.yaml", ("dicts/base",))
    source = tmp_path / "dicts/base.dict.yaml"
    rows = [("物体", "wuti", "800")]
    rows.extend((f"测试词{index}", f"ceshici{index}", str(index)) for index in range(204))
    _write_dictionary(source, rows=rows)

    index_dir = tmp_path / "indexes"
    CompactDictionaryIndex(
        index_dir / "wanxiang.sqlite", "wanxiang",
    ).build([CompactDictionarySource(source, "dicts/base.dict.yaml")])
    client = DataEngineClient(timeout_seconds=5)
    dictionary_index = VNextDictionaryIndex(tmp_path, client, index_dir)
    dialog = DictionaryCenterDialog(
        tmp_path, dictionary_index=dictionary_index,
    )
    try:
        assert dictionary_index.state == "ready"
        assert dictionary_index._storage_kind == "compact"
        row = next(
            row for row, item in enumerate(dialog._catalog)
            if item.relative_path == "dicts/base.dict.yaml"
        )

        dialog._catalog_table.selectRow(row)
        for _ in range(250):
            QTest.qWait(20)
            if dialog._entry_table.rowCount() == 200:
                break
        assert dialog._entry_table.rowCount() == 200
        assert dialog._load_more.isEnabled()
        assert "索引正在后台准备" not in dialog._status.text()

        dialog._load_more.click()
        for _ in range(250):
            QTest.qWait(20)
            if dialog._entry_table.rowCount() == 205:
                break
        assert dialog._entry_table.rowCount() == 205
        assert not dialog._load_more.isEnabled()

        dialog._search.setText("物体")
        for _ in range(250):
            QTest.qWait(20)
            if (
                dialog._entry_table.rowCount() == 1
                and dialog._entry_table.item(0, 0).text() == "物体"
            ):
                break
        assert dialog._entry_table.rowCount() == 1
        assert dialog._entry_table.item(0, 1).text() == "wuti"
        assert "索引正在后台准备" not in dialog._status.text()
    finally:
        dialog.dispose()
        client.close()
