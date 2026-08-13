from pathlib import Path
from time import perf_counter

import pytest

from src.service.compact_dictionary_index import (
    CompactDictionarySource,
    CompactIndexBuildCancelled,
)
from src.service.pinyin_service import PinyinService
from src.service.sharded_dictionary_index import ShardedDictionaryIndex


def _source(path: Path, name: str, rows: list[str]) -> CompactDictionarySource:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\n...\n" + "".join(rows), encoding="utf-8",
    )
    return CompactDictionarySource(path, name, 1.0)


def test_sharded_index_queries_and_browses_without_source_files(tmp_path):
    first = _source(
        tmp_path / "Rime" / "dicts" / "first.dict.yaml", "first",
        ["还\thái\t900\n", "物体\twù tǐ\t800\n"],
    )
    second = _source(
        tmp_path / "Rime" / "dicts" / "second.dict.yaml", "second",
        ["什么\tshen me\t700\n", "测试\tce shi\t600\n"],
    )
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    index.refresh([first, second], PinyinService().split_pinyin_code)

    first.path.unlink()
    second.path.unlink()

    assert [item.text for item in index.lookup_code("wti")] == ["物体"]
    assert [item.code for item in index.lookup_text("什么")] == ["shenme"]
    page1 = index.entries_page([], limit=3)
    page2 = index.entries_page([], limit=3, cursor=page1["next_cursor"])
    assert [item["text"] for item in page1["entries"] + page2["entries"]] == [
        "还", "物体", "什么", "测试",
    ]
    exact = index.entries_page([], query="ceshi", limit=20)
    assert [item["text"] for item in exact["entries"]] == ["测试"]


def test_refresh_rebuilds_only_changed_shard(tmp_path):
    first = _source(tmp_path / "first.dict.yaml", "first", ["甲\tjia\t1\n"])
    second = _source(tmp_path / "second.dict.yaml", "second", ["乙\tyi\t1\n"])
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    original = index.refresh([first, second])
    original_shards = {path.name: path.stat().st_mtime_ns for path in index.shard_dir.iterdir()}

    first.path.write_text(
        first.path.read_text(encoding="utf-8") + "新增\txin zeng\t2\n",
        encoding="utf-8",
    )
    refreshed = index.refresh([first, second])

    assert original.rebuilt_shards == 2
    assert refreshed.rebuilt_shards == 1
    assert refreshed.reused_shards == 1
    remaining = {path.name: path.stat().st_mtime_ns for path in index.shard_dir.iterdir()}
    assert all(remaining[name] == stamp for name, stamp in original_shards.items())
    assert [item.text for item in index.lookup_code("xz")] == ["新增"]


def test_cancelled_refresh_keeps_previous_catalog_and_query_results(tmp_path):
    source = _source(tmp_path / "base.dict.yaml", "base", ["旧词\tjiu ci\t1\n"])
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    index.refresh([source])
    catalog_before = index.catalog_path.read_bytes()
    source.path.write_text(
        "---\nname: base\n...\n" + "".join(
            f"新词{number}\txin ci\t{number}\n" for number in range(1000)
        ),
        encoding="utf-8",
    )
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(CompactIndexBuildCancelled):
        index.refresh([source], should_cancel=cancel)

    assert index.catalog_path.read_bytes() == catalog_before
    assert [item.text for item in index.lookup_code("jc")] == ["旧词"]
    assert not index.catalog_path.with_name("catalog.sqlite.building").exists()


def test_unchanged_refresh_writes_no_shards(tmp_path):
    source = _source(tmp_path / "base.dict.yaml", "base", ["测试\tce shi\t1\n"])
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    index.refresh([source])

    report = index.refresh([source])

    assert report.rebuilt_shards == 0
    assert report.reused_shards == 1
    assert report.processed_bytes == 0


def test_twenty_two_shards_keep_missing_query_bounded_without_source_scan(tmp_path):
    sources = []
    for file_number in range(22):
        sources.append(_source(
            tmp_path / "Rime" / "dicts" / f"part-{file_number}.dict.yaml",
            f"part-{file_number}",
            [
                f"词{file_number}-{row}\tci {file_number} {row}\t{row}\n"
                for row in range(500)
            ],
        ))
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    index.refresh(sources)
    for source in sources:
        source.path.unlink()

    started = perf_counter()
    result = index.lookup_text("绝不命中的词", limit=20)
    elapsed = perf_counter() - started

    assert result == []
    # Deliberately generous for shared Windows CI; the architectural contract
    # is bounded indexed reads, not source-size-proportional scanning.
    assert elapsed < 1.0


def test_corrupt_current_catalog_falls_back_to_previous_generation(tmp_path):
    source = _source(tmp_path / "base.dict.yaml", "base", ["旧词\tjiu ci\t1\n"])
    index = ShardedDictionaryIndex(tmp_path / "indexes" / "wanxiang", "wanxiang")
    index.refresh([source])
    source.path.write_text(
        "---\nname: base\n...\n新词\txin ci\t2\n", encoding="utf-8",
    )
    index.refresh([source])
    index.catalog_path.write_bytes(b"not a sqlite database")

    assert [item.text for item in index.lookup_code("jc")] == ["旧词"]
    assert index.lookup_code("xc") == []
