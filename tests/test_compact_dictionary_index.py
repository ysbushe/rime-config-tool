from pathlib import Path

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    CompactDictionarySource,
    CompactIndexBuildCancelled,
    discover_dictionary_sources,
)
from src.service.pinyin_service import PinyinService


def _make_rime(directory: Path) -> Path:
    rime = directory / "Rime"
    (rime / "dicts").mkdir(parents=True)
    (rime / "wanxiang.dict.yaml").write_text(
        "---\nname: wanxiang\nimport_tables:\n  - dicts/base\n...\n",
        encoding="utf-8",
    )
    (rime / "dicts" / "base.dict.yaml").write_text(
        "---\nname: base\n...\n"
        "还\thái\t900\n"
        "物体\twù tǐ\t800\n"
        "什么\tshen me\t700\n"
        "什么\tsha me\t600\n",
        encoding="utf-8",
    )
    return rime


def test_compact_index_exact_full_short_and_text_queries(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    index = CompactDictionaryIndex(tmp_path / "indexes" / "wanxiang.sqlite", "wanxiang")

    report = index.build(sources, PinyinService().split_pinyin_code)

    assert report.file_count == 2
    assert report.entry_count == 4
    assert index.is_current(sources)
    assert [item.text for item in index.lookup_code("h")] == ["还"]
    assert [item.text for item in index.lookup_code("wut")] == ["物体"]
    assert [item.text for item in index.lookup_code("wti")] == ["物体"]
    assert [item.text for item in index.lookup_code("wuti", include_shorthand=False)] == ["物体"]
    assert index.lookup_code("sm")[0].text == "什么"
    assert {item.code for item in index.lookup_text("什么")} == {"shenme", "shame"}
    assert index.lookup_code("wu") == []


def test_compact_index_refreshes_only_changed_source_and_removes_deleted(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    index = CompactDictionaryIndex(tmp_path / "wanxiang.sqlite", "wanxiang")
    index.build(sources, PinyinService().split_pinyin_code)
    base = rime / "dicts" / "base.dict.yaml"
    base.write_text(
        "---\nname: base\n...\n物体\twù tǐ\t999\n新增\txin zeng\t500\n",
        encoding="utf-8",
    )
    refreshed_sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])

    report = index.refresh(refreshed_sources, PinyinService().split_pinyin_code)

    assert not report.full_rebuild
    assert report.entry_count == 2
    assert index.lookup_code("h") == []
    assert index.lookup_code("wut")[0].weight == 999
    assert index.lookup_code("xz")[0].text == "新增"
    assert index.is_current(refreshed_sources)


def test_scheme_id_mismatch_never_reuses_another_scheme_index(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    path = tmp_path / "shared.sqlite"
    CompactDictionaryIndex(path, "wanxiang").build(sources)

    frost = CompactDictionaryIndex(path, "rime_frost")

    assert not frost.is_current(sources)
    assert frost.lookup_code("wuti") == []


def test_fingerprint_collisions_are_verified_against_original_values(tmp_path, monkeypatch):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    monkeypatch.setattr(
        "src.service.compact_dictionary_index._fingerprint",
        lambda _value, _domain: 7,
    )
    index = CompactDictionaryIndex(tmp_path / "collision.sqlite", "wanxiang")
    index.build(sources, PinyinService().split_pinyin_code)

    assert [item.text for item in index.lookup_text("还")] == ["还"]
    assert [item.text for item in index.lookup_code("wuti", include_shorthand=False)] == ["物体"]
    assert index.lookup_code("notfound") == []


def test_unchanged_refresh_does_not_create_a_copy(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    path = tmp_path / "wanxiang.sqlite"
    index = CompactDictionaryIndex(path, "wanxiang")
    index.build(sources)

    report = index.refresh(sources)

    assert not report.full_rebuild
    assert not path.with_name(path.name + ".refreshing").exists()


def test_compact_index_pages_large_exact_bucket_without_duplicates(tmp_path):
    source = tmp_path / "many.dict.yaml"
    source.write_text(
        "---\nname: many\n...\n" + "".join(
            f"候选{index}\th\t{100 - index}\n" for index in range(7)
        ),
        encoding="utf-8",
    )
    index = CompactDictionaryIndex(tmp_path / "many.sqlite", "wanxiang")
    index.build(
        [CompactDictionarySource(source, "wanxiang", 1.0)]
    )

    first = index.lookup_code_page("h", page_size=3)
    second = index.lookup_code_page("h", page_size=3, cursor=first.next_cursor)
    third = index.lookup_code_page("h", page_size=3, cursor=second.next_cursor)

    values = [
        item.text
        for page in (first, second, third)
        for item in page.candidates
    ]
    assert values == [f"候选{index}" for index in range(7)]
    assert len(set(values)) == 7
    assert first.next_cursor is not None
    assert second.next_cursor is not None
    assert third.next_cursor is None


def test_cancelled_build_keeps_existing_index_and_removes_temporary_file(tmp_path):
    original = tmp_path / "original.dict.yaml"
    original.write_text("---\nname: old\n...\n旧词\tjiu ci\t1\n", encoding="utf-8")
    path = tmp_path / "wanxiang.sqlite"
    index = CompactDictionaryIndex(path, "wanxiang")
    index.build([CompactDictionarySource(original, "wanxiang")])
    before = path.read_bytes()
    replacement = tmp_path / "replacement.dict.yaml"
    replacement.write_text(
        "---\nname: new\n...\n" + "".join(
            f"新词{item}\txin ci\t{item}\n" for item in range(2000)
        ),
        encoding="utf-8",
    )
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    try:
        index.build(
            [CompactDictionarySource(replacement, "wanxiang")],
            should_cancel=should_cancel,
        )
    except CompactIndexBuildCancelled:
        pass
    else:
        raise AssertionError("build should have been cancelled")

    assert path.read_bytes() == before
    assert not path.with_name(path.name + ".building").exists()


def test_build_progress_reports_bytes_and_final_phases(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    values = []

    CompactDictionaryIndex(
        tmp_path / "progress.sqlite", "wanxiang",
    ).build(sources, progress=values.append)

    assert values
    assert all("processed_bytes" in value and "total_bytes" in value for value in values)
    total = values[0]["total_bytes"]
    assert total > 0
    assert any(0 < value["processed_bytes"] <= total for value in values)
    assert [value["phase"] for value in values[-2:]] == ["optimizing", "switching"]


def test_incremental_refresh_reports_changed_file_bytes_and_final_phases(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    index = CompactDictionaryIndex(tmp_path / "refresh.sqlite", "wanxiang")
    index.build(sources)
    changed = sources[-1].path
    changed.write_text(
        changed.read_text(encoding="utf-8") + "新增词\txin zeng ci\t2\n",
        encoding="utf-8",
    )
    refreshed_sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    values = []

    report = index.refresh(refreshed_sources, progress=values.append)

    assert not report.full_rebuild
    building = [value for value in values if value["phase"] == "building"]
    assert building
    assert building[-1]["processed_bytes"] == building[-1]["total_bytes"]
    assert building[-1]["total_bytes"] == changed.stat().st_size
    assert [value["phase"] for value in values[-2:]] == ["optimizing", "switching"]


def test_compact_index_browses_and_exactly_filters_entries_without_source(tmp_path):
    rime = _make_rime(tmp_path)
    sources = discover_dictionary_sources(rime, [("wanxiang", 1.2)])
    index = CompactDictionaryIndex(tmp_path / "browse.sqlite", "wanxiang")
    index.build(sources, PinyinService().split_pinyin_code)
    for source in sources:
        source.path.unlink()

    first = index.entries_page(page_size=2)
    second = index.entries_page(page_size=2, cursor=first.next_cursor)
    assert [item.text for item in first.entries + second.entries] == [
        "还", "物体", "什么", "什么",
    ]
    assert first.next_cursor is not None
    assert second.next_cursor is None
    assert [item.text for item in index.entries_page("wuti").entries] == ["物体"]


def test_compact_index_browse_can_filter_one_source_without_source_files(tmp_path):
    first = tmp_path / "first.dict.yaml"
    second = tmp_path / "second.dict.yaml"
    first.write_text("---\nname: first\n...\n甲\tjia\t1\n", encoding="utf-8")
    second.write_text("---\nname: second\n...\n乙\tyi\t2\n", encoding="utf-8")
    index = CompactDictionaryIndex(tmp_path / "filtered.sqlite", "wanxiang")
    index.build([
        CompactDictionarySource(first, "first"),
        CompactDictionarySource(second, "second"),
    ])
    first.unlink()
    second.unlink()

    page = index.entries_page(source_paths=[second])

    assert [item.text for item in page.entries] == ["乙"]
