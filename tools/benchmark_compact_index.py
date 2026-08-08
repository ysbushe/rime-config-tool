"""Build and measure the VNext compact dictionary index prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    discover_dictionary_sources,
)
from src.service.pinyin_service import PinyinService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rime-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scheme", default="wanxiang")
    parser.add_argument(
        "--root", action="append", default=[],
        help="Dictionary root as NAME or NAME:QUALITY; repeat as needed.",
    )
    args = parser.parse_args()
    roots = [_parse_root(value) for value in (args.root or [args.scheme])]
    sources = discover_dictionary_sources(args.rime_dir, roots)
    if not sources:
        parser.error("no dictionary sources were discovered")

    pinyin = PinyinService()
    index = CompactDictionaryIndex(args.output, args.scheme)
    report = index.build(sources, pinyin.split_pinyin_code)
    timings = {}
    samples = {
        "full_wuti": lambda: index.lookup_code("wuti", limit=20, splitter=pinyin.split_pinyin_code),
        "short_h": lambda: index.lookup_code("h", limit=20, splitter=pinyin.split_pinyin_code),
        "short_wut": lambda: index.lookup_code("wut", limit=20, splitter=pinyin.split_pinyin_code),
        "short_wti": lambda: index.lookup_code("wti", limit=20, splitter=pinyin.split_pinyin_code),
        "text_物体": lambda: index.lookup_text("物体", limit=20),
        "missing": lambda: index.lookup_code("codexnotfound", limit=20),
    }
    results = {}
    for name, operation in samples.items():
        durations = []
        rows = []
        for _ in range(21):
            started = perf_counter()
            rows = operation()
            durations.append((perf_counter() - started) * 1000)
        timings[name] = round(median(durations), 3)
        results[name] = [row.text for row in rows[:6]]

    print(json.dumps({
        "scheme": args.scheme,
        "source_files": report.file_count,
        "source_bytes": sum(source.path.stat().st_size for source in sources),
        "entries": report.entry_count,
        "texts": report.text_count,
        "codes_and_aliases": report.code_count,
        "index_bytes": report.size_bytes,
        "build_seconds": round(report.elapsed_seconds, 3),
        "median_query_ms": timings,
        "sample_results": results,
    }, ensure_ascii=False, indent=2))
    return 0


def _parse_root(value: str) -> tuple[str, float]:
    name, separator, quality = value.partition(":")
    return name, float(quality) if separator else 1.0


if __name__ == "__main__":
    raise SystemExit(main())
