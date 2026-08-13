"""Immutable per-source SQLite shards with an atomically switched catalog."""
from __future__ import annotations

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
from time import perf_counter
from typing import Callable, Iterable

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    CompactDictionarySource,
    CompactIndexBuildCancelled,
    CompactDictionaryCandidate,
)


_CATALOG_VERSION = 1


@dataclass(frozen=True)
class ShardedIndexReport:
    path: Path
    elapsed_seconds: float
    size_bytes: int
    file_count: int
    entry_count: int
    rebuilt_shards: int
    reused_shards: int
    processed_bytes: int


class ShardedDictionaryIndex:
    """Build changed source files as immutable shards and switch a tiny catalog."""

    def __init__(self, directory: str | Path, scheme_id: str) -> None:
        self.directory = Path(directory)
        self.scheme_id = str(scheme_id or "").strip()
        if not self.scheme_id:
            raise ValueError("scheme_id不能为空")
        self.catalog_path = self.directory / "catalog.sqlite"
        self.shard_dir = self.directory / "shards"
        self._query_executor: ThreadPoolExecutor | None = None

    def refresh(
        self,
        sources: Iterable[CompactDictionarySource],
        splitter: Callable[[str], list[str]] | None = None,
        *,
        progress: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ShardedIndexReport:
        started = perf_counter()
        normalized = sorted(
            (CompactDictionarySource(Path(item.path).resolve(), item.source, item.quality)
             for item in sources),
            key=lambda item: str(item.path).casefold(),
        )
        if not normalized:
            raise ValueError("未发现可构建索引的词典源文件")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.shard_dir.mkdir(parents=True, exist_ok=True)
        previous = self._catalog_rows()
        total_bytes = sum(item.path.stat().st_size for item in normalized)
        processed_bytes = 0
        rebuilt_bytes = 0
        rebuilt = 0
        reused = 0
        created: list[Path] = []
        rows: list[dict] = []
        temporary_catalog = self.catalog_path.with_name("catalog.sqlite.building")
        previous_catalog = self.catalog_path.with_name("catalog.previous.sqlite")
        temporary_catalog.unlink(missing_ok=True)
        common_root = Path(os.path.commonpath([str(item.path.parent) for item in normalized]))
        switched = False
        try:
            for position, source in enumerate(normalized):
                self._check_cancelled(should_cancel)
                stat = source.path.stat()
                source_path = str(source.path)
                old = previous.get(source_path)
                signature = (
                    stat.st_size, stat.st_mtime_ns, source.source, float(source.quality),
                )
                shard_name = str(old["shard_name"]) if old and (
                    int(old["size"]), int(old["mtime_ns"]), str(old["source"]),
                    float(old["quality"]),
                ) == signature else self._shard_name(source, stat.st_size, stat.st_mtime_ns)
                shard_path = self.shard_dir / shard_name
                if old and shard_path.is_file() and shard_name == old["shard_name"]:
                    entry_count = int(old["entry_count"])
                    reused += 1
                else:
                    self._notify(
                        progress, "building", position, len(normalized), source.path.name,
                        processed_bytes, total_bytes,
                    )
                    report = CompactDictionaryIndex(shard_path, self.scheme_id).build(
                        [source], splitter,
                        should_cancel=should_cancel,
                    )
                    entry_count = report.entry_count
                    created.append(shard_path)
                    rebuilt += 1
                    rebuilt_bytes += stat.st_size
                processed_bytes += stat.st_size
                try:
                    relative_path = source.path.relative_to(common_root).as_posix()
                except ValueError:
                    relative_path = source.path.name
                rows.append({
                    "ordinal": position,
                    "source_path": source_path,
                    "relative_path": relative_path,
                    "shard_name": shard_name,
                    "source": source.source,
                    "quality": float(source.quality),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "entry_count": entry_count,
                })
                self._notify(
                    progress, "building", position + 1, len(normalized), source.path.name,
                    processed_bytes, total_bytes,
                )
            self._check_cancelled(should_cancel)
            self._write_catalog(temporary_catalog, rows)
            self._check_cancelled(should_cancel)
            self._notify(
                progress, "switching", len(normalized), len(normalized), "",
                processed_bytes, total_bytes,
            )
            if self.catalog_path.is_file():
                shutil.copy2(self.catalog_path, previous_catalog)
            os.replace(temporary_catalog, self.catalog_path)
            switched = True
        except Exception:
            temporary_catalog.unlink(missing_ok=True)
            if not switched:
                active = {str(row["shard_name"]) for row in previous.values()}
                for path in created:
                    if path.name not in active:
                        path.unlink(missing_ok=True)
            raise
        self._cleanup_obsolete_shards(rows, previous)
        size_bytes = self.catalog_path.stat().st_size + sum(
            (self.shard_dir / str(row["shard_name"])).stat().st_size for row in rows
        )
        return ShardedIndexReport(
            self.catalog_path, perf_counter() - started, size_bytes, len(rows),
            sum(int(row["entry_count"]) for row in rows), rebuilt, reused,
            rebuilt_bytes,
        )

    def is_current(self, sources: Iterable[CompactDictionarySource]) -> bool:
        rows = self._catalog_rows()
        values = list(sources)
        if len(rows) != len(values):
            return False
        for item in values:
            path = str(Path(item.path).resolve())
            row = rows.get(path)
            try:
                stat = Path(item.path).stat()
            except OSError:
                return False
            if not row or (
                int(row["size"]), int(row["mtime_ns"]), str(row["source"]),
                float(row["quality"]),
            ) != (stat.st_size, stat.st_mtime_ns, item.source, float(item.quality)):
                return False
            if not (self.shard_dir / str(row["shard_name"])).is_file():
                return False
        return True

    def is_available(self) -> bool:
        rows = self._catalog_rows()
        return bool(rows) and all(
            (self.shard_dir / str(row["shard_name"])).is_file()
            for row in rows.values()
        )

    def close(self) -> None:
        executor, self._query_executor = self._query_executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return

    def lookup_text(self, query: str, limit: int = 20) -> list[CompactDictionaryCandidate]:
        return self._lookup("text", query, limit)

    def lookup_code(
        self,
        query: str,
        limit: int = 20,
        include_shorthand: bool = True,
        splitter: Callable[[str], list[str]] | None = None,
    ) -> list[CompactDictionaryCandidate]:
        return self._lookup("code", query, limit, include_shorthand, splitter)

    def entries_page(
        self,
        paths: Iterable[str],
        query: str = "",
        limit: int = 200,
        cursor: dict | None = None,
    ) -> dict:
        selected = {str(Path(value).resolve()) for value in paths}
        rows = [row for row in self._ordered_rows() if not selected or row["source_path"] in selected]
        start_shard = max(0, int((cursor or {}).get("shard_index", 0)))
        local_cursor = max(0, int((cursor or {}).get("record_id", 0)))
        accepted = []
        for shard_index in range(start_shard, len(rows)):
            row = rows[shard_index]
            page = CompactDictionaryIndex(
                self.shard_dir / str(row["shard_name"]), self.scheme_id,
            ).entries_page(
                query, limit - len(accepted), local_cursor if shard_index == start_shard else 0,
            )
            accepted.extend({
                "text": item.text,
                "code": item.code,
                "weight": "" if item.weight is None else str(item.weight),
                "source_file": str(row["relative_path"]),
            } for item in page.entries)
            if page.next_cursor is not None:
                return {
                    "entries": accepted,
                    "next_cursor": {
                        "shard_index": shard_index,
                        "record_id": page.next_cursor,
                    },
                }
            if len(accepted) >= limit:
                next_cursor = None
                if shard_index + 1 < len(rows):
                    next_cursor = {"shard_index": shard_index + 1, "record_id": 0}
                return {"entries": accepted[:limit], "next_cursor": next_cursor}
        return {"entries": accepted, "next_cursor": None}

    def _lookup(
        self,
        kind: str,
        query: str,
        limit: int,
        include_shorthand: bool = True,
        splitter: Callable[[str], list[str]] | None = None,
    ) -> list[CompactDictionaryCandidate]:
        size = max(1, min(500, int(limit)))
        rows = self._ordered_rows()

        def read(row: dict) -> list[CompactDictionaryCandidate]:
            shard = CompactDictionaryIndex(
                self.shard_dir / str(row["shard_name"]), self.scheme_id,
            )
            return (
                shard.lookup_text(query, size)
                if kind == "text"
                else shard.lookup_code(query, size, include_shorthand, splitter)
            )

        if not rows:
            return []
        if self._query_executor is None:
            self._query_executor = ThreadPoolExecutor(
                max_workers=min(8, len(rows)),
                thread_name_prefix=f"dictionary-query-{self.scheme_id}",
            )
        result = []
        for values in self._query_executor.map(read, rows):
            result.extend(values)
        result.sort(
            key=lambda item: (
                item.match_kind == "full", item.quality, item.weight or 0,
            ),
            reverse=True,
        )
        return result[:size]

    def _ordered_rows(self) -> list[dict]:
        return sorted(self._catalog_rows().values(), key=lambda row: int(row["ordinal"]))

    def _catalog_rows(self) -> dict[str, dict]:
        rows = self._read_catalog(self.catalog_path)
        if rows:
            return rows
        return self._read_catalog(
            self.catalog_path.with_name("catalog.previous.sqlite"),
        )

    def _read_catalog(self, path: Path) -> dict[str, dict]:
        if not path.is_file():
            return {}
        try:
            uri = f"{path.resolve().as_uri()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                version = db.execute(
                    "SELECT value FROM metadata WHERE key='catalog_version'"
                ).fetchone()
                scheme = db.execute(
                    "SELECT value FROM metadata WHERE key='scheme_id'"
                ).fetchone()
                if not version or int(version[0]) != _CATALOG_VERSION:
                    return {}
                if not scheme or str(scheme[0]) != self.scheme_id:
                    return {}
                columns = [item[1] for item in db.execute("PRAGMA table_info(shards)")]
                return {
                    str(values[columns.index("source_path")]): dict(zip(columns, values))
                    for values in db.execute("SELECT * FROM shards")
                }
        except (OSError, sqlite3.Error, ValueError):
            return {}

    def _write_catalog(self, path: Path, rows: list[dict]) -> None:
        with closing(sqlite3.connect(path)) as db:
            db.executescript(
                """
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
                CREATE TABLE shards (
                    ordinal INTEGER NOT NULL,
                    source_path TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    shard_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    quality REAL NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    entry_count INTEGER NOT NULL
                ) WITHOUT ROWID;
                """
            )
            db.executemany(
                "INSERT INTO metadata(key,value) VALUES (?,?)",
                (("catalog_version", str(_CATALOG_VERSION)), ("scheme_id", self.scheme_id)),
            )
            db.executemany(
                "INSERT INTO shards(ordinal,source_path,relative_path,shard_name,source,quality,size,mtime_ns,entry_count) "
                "VALUES (:ordinal,:source_path,:relative_path,:shard_name,:source,:quality,:size,:mtime_ns,:entry_count)",
                rows,
            )
            db.commit()

    def _shard_name(self, source: CompactDictionarySource, size: int, mtime_ns: int) -> str:
        identity = "\0".join((
            str(_CATALOG_VERSION), str(source.path), str(size), str(mtime_ns),
            source.source, repr(float(source.quality)),
        )).encode("utf-8")
        return f"{hashlib.sha256(identity).hexdigest()[:24]}.sqlite"

    def _cleanup_obsolete_shards(
        self, active_rows: list[dict], previous_rows: dict[str, dict],
    ) -> None:
        retained = {str(row["shard_name"]) for row in active_rows}
        retained.update(str(row["shard_name"]) for row in previous_rows.values())
        for path in self.shard_dir.glob("*.sqlite"):
            if path.name not in retained:
                try:
                    path.unlink()
                except OSError:
                    # A read-only query may briefly hold the old file on Windows.
                    continue

    @staticmethod
    def _check_cancelled(callback: Callable[[], bool] | None) -> None:
        if callback is not None and callback():
            raise CompactIndexBuildCancelled("索引构建已取消")

    @staticmethod
    def _notify(callback, phase, completed, total, current, processed, total_bytes) -> None:
        if callback is not None:
            callback({
                "phase": phase,
                "files_completed": completed,
                "files_total": total,
                "current_file": current,
                "current_entries": 0,
                "processed_bytes": processed,
                "total_bytes": total_bytes,
            })
