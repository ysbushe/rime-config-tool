"""Build the v3 shard index in a temporary directory and report read-only timings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.service.compact_dictionary_index import discover_dictionary_sources
from src.service.dictionary_catalog import DictionaryCatalogService
from src.service.pinyin_service import PinyinService
from src.service.rime_profiles import scheme_dictionary_roots
from src.service.sharded_dictionary_index import ShardedDictionaryIndex


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rime-dir", required=True)
    args = parser.parse_args()
    rime_dir = Path(args.rime_dir).resolve()
    if not rime_dir.is_dir():
        raise SystemExit("Rime directory does not exist")
    scheme_id = DictionaryCatalogService(rime_dir).preferred_scheme()
    roots = scheme_dictionary_roots(scheme_id)
    sources = discover_dictionary_sources(rime_dir, roots)
    if not sources:
        raise SystemExit("No dictionary sources discovered")
    source_bytes = sum(item.path.stat().st_size for item in sources)
    pinyin = PinyinService()

    with TemporaryDirectory(prefix="rime-config-sharded-benchmark-") as temporary:
        index = ShardedDictionaryIndex(Path(temporary) / scheme_id, scheme_id)

        def progress(value: dict) -> None:
            if value.get("phase") == "building" and value.get("files_completed"):
                print(
                    f"progress {value['files_completed']}/{value['files_total']} "
                    f"{value.get('current_file', '')}",
                    flush=True,
                )

        report = index.refresh(
            sources, pinyin.split_pinyin_code, progress=progress,
        )
        query_values = ("h", "wut", "wti", "wuti", "绝不命中的编码")
        query_ms = {}
        query_p95_ms = {}
        for value in query_values:
            samples = []
            for _ in range(7):
                started = perf_counter()
                index.lookup_code(value, 20, True, pinyin.split_pinyin_code)
                samples.append((perf_counter() - started) * 1000)
            query_ms[value] = round(median(samples), 2)
            query_p95_ms[value] = round(sorted(samples)[-1], 2)
        started = perf_counter()
        missing_text = index.lookup_text("绝不命中的词", 20)
        missing_text_ms = round((perf_counter() - started) * 1000, 2)
        started = perf_counter()
        first_page = index.entries_page([], limit=200)
        browse_200_ms = round((perf_counter() - started) * 1000, 2)

        changed_source = min(sources, key=lambda item: item.path.stat().st_size)
        shadow = Path(temporary) / "changed" / changed_source.path.name
        shadow.parent.mkdir()
        shutil.copy2(changed_source.path, shadow)
        with shadow.open("a", encoding="utf-8") as stream:
            stream.write("基准测试临时词\tji zhun ce shi\t1\n")
        changed_sources = [
            type(item)(shadow, item.source, item.quality)
            if item.path == changed_source.path else item
            for item in sources
        ]
        incremental = index.refresh(changed_sources, pinyin.split_pinyin_code)
        print(json.dumps({
            "scheme_id": scheme_id,
            "source_files": len(sources),
            "source_bytes": source_bytes,
            "index_bytes": report.size_bytes,
            "entry_count": report.entry_count,
            "build_seconds": round(report.elapsed_seconds, 2),
            "query_median_ms": query_ms,
            "query_p95_ms": query_p95_ms,
            "missing_text_ms": missing_text_ms,
            "missing_text_count": len(missing_text),
            "browse_200_count": len(first_page["entries"]),
            "browse_200_ms": browse_200_ms,
            "incremental_rebuilt_shards": incremental.rebuilt_shards,
            "incremental_reused_shards": incremental.reused_shards,
            "incremental_processed_bytes": incremental.processed_bytes,
            "incremental_seconds": round(incremental.elapsed_seconds, 2),
        }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
