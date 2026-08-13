"""VNext scheme-scoped compact dictionary index prototype.

The prototype deliberately has no UI dependency.  It builds one SQLite file per
Rime scheme, stores full codes in a reversible 5-bit representation and keeps a
single indexed strict-shorthand key.  The other supported shorthand forms are
verified from syllable boundaries after that exact indexed lookup, avoiding
millions of duplicated alias strings.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from time import perf_counter
from typing import Callable, Iterable, Iterator, Sequence
import unicodedata
import zlib

from src.encoding.code_suggestions import raw_code


_SCHEMA_VERSION = 5
_BATCH_SIZE = 256
_MAX_SHORTHAND_TEXT_LENGTH = 8


@dataclass(frozen=True)
class CompactDictionarySource:
    path: Path
    source: str
    quality: float = 1.0


@dataclass(frozen=True)
class CompactDictionaryCandidate:
    text: str
    code: str
    source: str
    weight: int | None
    quality: float
    match_kind: str = "full"


@dataclass(frozen=True)
class CompactIndexReport:
    path: Path
    elapsed_seconds: float
    size_bytes: int
    file_count: int
    entry_count: int
    text_count: int
    code_count: int
    full_rebuild: bool


@dataclass(frozen=True)
class CompactLookupPage:
    candidates: list[CompactDictionaryCandidate]
    next_cursor: int | None


@dataclass(frozen=True)
class CompactDictionaryEntry:
    text: str
    code: str
    weight: int | None
    record_id: int


@dataclass(frozen=True)
class CompactEntryPage:
    entries: list[CompactDictionaryEntry]
    next_cursor: int | None


class CompactIndexBuildCancelled(RuntimeError):
    """Raised when a caller cancels a build before its atomic switch."""


ProgressCallback = Callable[[dict], None]
CancelCallback = Callable[[], bool]


class CompactDictionaryIndex:
    """Build and query an isolated compact index for one Rime scheme."""

    def __init__(self, path: str | Path, scheme_id: str) -> None:
        scheme = _safe_scheme_id(scheme_id)
        if not scheme:
            raise ValueError("scheme_id must contain at least one safe character")
        self.path = Path(path)
        self.scheme_id = scheme

    def build(
        self,
        sources: Iterable[CompactDictionarySource],
        splitter: Callable[[str], list[str]] | None = None,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> CompactIndexReport:
        """Build a new database beside the target and atomically switch it in."""
        started = perf_counter()
        normalized_sources = _normalise_sources(sources)
        total_bytes = sum(source.path.stat().st_size for source in normalized_sources)
        processed_bytes = 0
        temporary = self.path.with_name(self.path.name + ".building")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
        try:
            _check_cancelled(should_cancel)
            _notify_progress(
                progress, "building", 0, len(normalized_sources), "",
                processed_bytes=0, total_bytes=total_bytes,
            )
            with closing(sqlite3.connect(temporary)) as db:
                _configure_builder(db)
                _create_schema(db)
                db.execute(
                    "INSERT INTO metadata(key, value) VALUES ('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
                db.execute(
                    "INSERT INTO metadata(key, value) VALUES ('scheme_id', ?)",
                    (self.scheme_id,),
                )
                for position, source in enumerate(normalized_sources):
                    _check_cancelled(should_cancel)
                    file_id = _insert_file(db, source)
                    _insert_source_entries(
                        db, file_id, source.path, splitter,
                        should_cancel=should_cancel,
                        progress=progress,
                        files_completed=position,
                        files_total=len(normalized_sources),
                        processed_bytes=processed_bytes,
                        total_bytes=total_bytes,
                    )
                    processed_bytes += source.path.stat().st_size
                    _notify_progress(
                        progress, "building", position + 1,
                        len(normalized_sources), source.path.name,
                        processed_bytes=processed_bytes, total_bytes=total_bytes,
                    )
                _check_cancelled(should_cancel)
                _write_manifest(db, normalized_sources, incremental_updates=0)
                _notify_progress(
                    progress, "optimizing", len(normalized_sources),
                    len(normalized_sources), "",
                    processed_bytes=total_bytes, total_bytes=total_bytes,
                )
                _create_indexes(db)
                db.execute("ANALYZE")
                db.commit()
            _notify_progress(
                progress, "switching", len(normalized_sources),
                len(normalized_sources), "",
                processed_bytes=total_bytes, total_bytes=total_bytes,
            )
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return self._report(perf_counter() - started, full_rebuild=True)

    def refresh(
        self,
        sources: Iterable[CompactDictionarySource],
        splitter: Callable[[str], list[str]] | None = None,
        *,
        progress: ProgressCallback | None = None,
        should_cancel: CancelCallback | None = None,
    ) -> CompactIndexReport:
        """Refresh only changed files in a copied database, then atomically switch."""
        normalized_sources = _normalise_sources(sources)
        if not self._is_compatible():
            return self.build(
                normalized_sources, splitter,
                progress=progress, should_cancel=should_cancel,
            )

        started = perf_counter()
        if self.is_current(normalized_sources):
            return self._report(perf_counter() - started, full_rebuild=False)
        temporary = self.path.with_name(self.path.name + ".refreshing")
        temporary.unlink(missing_ok=True)
        try:
            _check_cancelled(should_cancel)
            _notify_progress(progress, "copying", 0, len(normalized_sources), "")
            _copy_with_cancellation(self.path, temporary, should_cancel)
            no_changes = False
            rebuild = False
            with closing(sqlite3.connect(temporary)) as db:
                db.execute("PRAGMA foreign_keys = ON")
                previous = {
                    row[1]: row
                    for row in db.execute(
                        "SELECT file_id, path, size, mtime_ns, source, quality FROM files"
                    )
                }
                current = {str(item.path): item for item in normalized_sources}
                changed = [
                    item for path, item in current.items()
                    if path not in previous
                    or previous[path][2:] != (
                        item.path.stat().st_size,
                        item.path.stat().st_mtime_ns,
                        item.source,
                        item.quality,
                    )
                ]
                removed = set(previous) - set(current)
                if not changed and not removed:
                    no_changes = True
                else:
                    update_count = int(_metadata_value(db, "incremental_updates", "0"))
                    rebuild = (
                        update_count >= 8
                        or len(changed) + len(removed) > max(4, len(current) // 3)
                    )
                    if not rebuild:
                        changed_total_bytes = sum(
                            source.path.stat().st_size for source in changed
                        )
                        changed_processed_bytes = 0
                        with db:
                            for path in removed:
                                _check_cancelled(should_cancel)
                                _delete_file_data(db, int(previous[path][0]))
                                db.execute("DELETE FROM files WHERE path = ?", (path,))
                            for position, source in enumerate(changed):
                                _check_cancelled(should_cancel)
                                path = str(source.path)
                                old = previous.get(path)
                                if old:
                                    file_id = int(old[0])
                                    _delete_file_data(db, file_id)
                                    stat = source.path.stat()
                                    db.execute(
                                        "UPDATE files SET source=?, quality=?, size=?, mtime_ns=? "
                                        "WHERE file_id=?",
                                        (source.source, source.quality, stat.st_size, stat.st_mtime_ns, file_id),
                                    )
                                else:
                                    file_id = _insert_file(db, source)
                                _insert_source_entries(
                                    db, file_id, source.path, splitter,
                                    should_cancel=should_cancel,
                                    progress=progress,
                                    files_completed=position,
                                    files_total=len(changed),
                                    processed_bytes=changed_processed_bytes,
                                    total_bytes=changed_total_bytes,
                                )
                                changed_processed_bytes += source.path.stat().st_size
                                _notify_progress(
                                    progress, "building", position + 1,
                                    len(changed), source.path.name,
                                    processed_bytes=changed_processed_bytes,
                                    total_bytes=changed_total_bytes,
                                )
                            _write_manifest(db, normalized_sources, update_count + 1)
                            _notify_progress(
                                progress, "optimizing", len(changed), len(changed), "",
                                processed_bytes=changed_total_bytes,
                                total_bytes=changed_total_bytes,
                            )
                            db.execute("ANALYZE")
            if no_changes:
                temporary.unlink(missing_ok=True)
                return self._report(perf_counter() - started, full_rebuild=False)
            if rebuild:
                temporary.unlink(missing_ok=True)
                return self.build(
                    normalized_sources, splitter,
                    progress=progress, should_cancel=should_cancel,
                )
            _check_cancelled(should_cancel)
            _notify_progress(
                progress, "switching", len(changed), len(changed), "",
                processed_bytes=changed_total_bytes,
                total_bytes=changed_total_bytes,
            )
            os.replace(temporary, self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return self._report(perf_counter() - started, full_rebuild=False)

    def lookup_text(self, text: str, limit: int = 20) -> list[CompactDictionaryCandidate]:
        value = (text or "").strip()
        if not value or not self._is_compatible():
            return []
        with closing(self._connect_readonly()) as db:
            file_ids = [row[0] for row in db.execute("SELECT file_id FROM files")]
            placeholders = ",".join("?" for _ in file_ids)
            rows = db.execute(
                "SELECT l.record_id, f.source, f.quality "
                "FROM lookups l JOIN files f ON f.file_id=l.file_id "
                f"WHERE l.file_id IN ({placeholders}) AND l.lookup_key=? "
                "LIMIT ?",
                (*file_ids, _fingerprint(_pack_text(value), 1), max(500, limit * 25)),
            ).fetchall()
            records = _resolve_records(db, rows)
        result = []
        for text_value, code_value, unit_lengths, weight, source, quality, record_id in records:
            if text_value != value:
                continue
            result.append(CompactDictionaryCandidate(
                value, code_value, source, weight, quality, "text",
            ))
        result.sort(key=lambda item: (item.quality, item.weight or 0), reverse=True)
        return result[:max(1, limit)]

    def lookup_code(
        self, code: str, limit: int = 20, include_shorthand: bool = True,
        splitter: Callable[[str], list[str]] | None = None,
    ) -> list[CompactDictionaryCandidate]:
        value = _normalise_code(code)
        if not value or not self._is_compatible():
            return []
        with closing(self._connect_readonly()) as db:
            file_ids = [row[0] for row in db.execute("SELECT file_id FROM files")]
            keys = {_fingerprint(_pack_code(value), 2)}
            if include_shorthand:
                keys.update({
                    _fingerprint(_pack_code(item), 3)
                    for item in _strict_query_keys(value, splitter)
                })
            ordered_keys = sorted(keys)
            file_placeholders = ",".join("?" for _ in file_ids)
            key_placeholders = ",".join("?" for _ in ordered_keys)
            scan_limit = max(500, max(1, limit) * 25)
            rows = db.execute(
                "SELECT l.record_id, f.source, f.quality "
                "FROM lookups l JOIN files f ON f.file_id=l.file_id "
                f"WHERE l.file_id IN ({file_placeholders}) "
                f"AND l.lookup_key IN ({key_placeholders}) "
                "LIMIT ?",
                (*file_ids, *ordered_keys, scan_limit),
            ).fetchall()
            records = _resolve_records(db, rows)

        candidates: list[CompactDictionaryCandidate] = []
        seen: set[tuple[str, str, str]] = set()
        for text_value, full_code, unit_lengths, weight, source, quality, record_id in records:
            if full_code == value:
                match_kind = "full"
            else:
                forms = _forms_from_lengths(full_code, unit_lengths)
                try:
                    match_kind = forms[value]
                except KeyError:
                    continue
            candidate = CompactDictionaryCandidate(
                text_value, full_code, source, weight, quality, match_kind,
            )
            key = (candidate.text, candidate.code, candidate.source)
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (
                item.match_kind == "full", item.quality, item.weight or 0,
            ),
            reverse=True,
        )
        return candidates[:max(1, limit)]

    def lookup_text_page(
        self, text: str, page_size: int = 20, cursor: int | None = None,
    ) -> CompactLookupPage:
        """Return one exact-match page without materialising the entire bucket."""
        value = (text or "").strip()
        size = max(1, min(500, int(page_size)))
        if not value or not self._is_compatible():
            return CompactLookupPage([], None)
        key = _fingerprint(_pack_text(value), 1)

        def convert(record) -> CompactDictionaryCandidate | None:
            text_value, code_value, _lengths, weight, source, quality, _record_id = record
            if text_value != value:
                return None
            return CompactDictionaryCandidate(
                text_value, code_value, source, weight, quality, "text",
            )

        return self._lookup_page({key}, size, cursor, convert)

    def lookup_code_page(
        self,
        code: str,
        page_size: int = 20,
        cursor: int | None = None,
        include_shorthand: bool = True,
        splitter: Callable[[str], list[str]] | None = None,
    ) -> CompactLookupPage:
        """Return one verified full/strict-shorthand page using a seek cursor."""
        value = _normalise_code(code)
        size = max(1, min(500, int(page_size)))
        if not value or not self._is_compatible():
            return CompactLookupPage([], None)
        keys = {_fingerprint(_pack_code(value), 2)}
        if include_shorthand:
            keys.update(
                _fingerprint(_pack_code(item), 3)
                for item in _strict_query_keys(value, splitter)
            )

        def convert(record) -> CompactDictionaryCandidate | None:
            text_value, full_code, unit_lengths, weight, source, quality, _record_id = record
            if full_code == value:
                match_kind = "full"
            else:
                match_kind = _forms_from_lengths(full_code, unit_lengths).get(value)
                if match_kind is None:
                    return None
            return CompactDictionaryCandidate(
                text_value, full_code, source, weight, quality, match_kind,
            )

        return self._lookup_page(keys, size, cursor, convert)

    def entries_page(
        self,
        query: str = "",
        page_size: int = 200,
        cursor: int | None = None,
        source_paths: Iterable[str | Path] | None = None,
    ) -> CompactEntryPage:
        """Browse or exactly filter entries without reopening the source dictionary."""
        size = max(1, min(500, int(page_size)))
        start = max(0, int(cursor or 0))
        if not self._is_compatible():
            return CompactEntryPage([], None)
        value = (query or "").strip()
        with closing(self._connect_readonly()) as db:
            file_ids = self._selected_file_ids(db, source_paths)
            if not file_ids:
                return CompactEntryPage([], None)
            if value:
                rows = self._exact_entry_records(
                    db, value, start, size + 1, file_ids,
                )
            else:
                rows = self._browse_entry_records(db, start, size + 1, file_ids)
        has_more = len(rows) > size
        visible = rows[:size]
        entries = [
            CompactDictionaryEntry(text, code, weight, record_id)
            for text, code, _lengths, weight, _source, _quality, record_id in visible
        ]
        return CompactEntryPage(
            entries,
            entries[-1].record_id if has_more and entries else None,
        )

    @staticmethod
    def _browse_entry_records(
        db: sqlite3.Connection, cursor: int, limit: int, file_ids: list[int],
    ) -> list[tuple]:
        block_cursor = cursor >> 8
        placeholders = ",".join("?" for _ in file_ids)
        rows = db.execute(
            "SELECT b.block_id, b.payload, f.source, f.quality "
            "FROM blocks b JOIN files f ON f.file_id=b.file_id "
            f"WHERE b.block_id>=? AND b.file_id IN ({placeholders}) "
            "ORDER BY b.block_id",
            (block_cursor, *file_ids),
        )
        result = []
        for block_id, payload, source, quality in rows:
            values = json.loads(zlib.decompress(payload).decode("utf-8"))
            for offset, (text, code, lengths, weight) in enumerate(values):
                record_id = (int(block_id) << 8) | offset
                if record_id <= cursor:
                    continue
                result.append((
                    text, code, bytes(lengths), weight, source, quality, record_id,
                ))
                if len(result) >= limit:
                    return result
        return result

    @staticmethod
    def _exact_entry_records(
        db: sqlite3.Connection, query: str, cursor: int, limit: int,
        file_ids: list[int],
    ) -> list[tuple]:
        text_value = query.casefold()
        code_value = _normalise_code(query)
        keys = {_fingerprint(_pack_text(query), 1)}
        if code_value:
            keys.add(_fingerprint(_pack_code(code_value), 2))
        scan_cursor = cursor
        result = []
        while len(result) < limit:
            rows = _seek_lookup_rows(
                db, file_ids, keys, scan_cursor, max(500, limit * 25),
            )
            if not rows:
                break
            records = _resolve_records(db, rows)
            for record in records:
                text, code, _lengths, _weight, _source, _quality, record_id = record
                scan_cursor = max(scan_cursor, int(record_id))
                if int(record_id) <= cursor:
                    continue
                if text.casefold() == text_value or _normalise_code(code) == code_value:
                    result.append(record)
                    if len(result) >= limit:
                        break
            if len(rows) < max(500, limit * 25):
                break
        result.sort(key=lambda item: item[-1])
        return result

    @staticmethod
    def _selected_file_ids(
        db: sqlite3.Connection,
        source_paths: Iterable[str | Path] | None,
    ) -> list[int]:
        rows = [(int(row[0]), str(row[1])) for row in db.execute(
            "SELECT file_id, path FROM files"
        )]
        if source_paths is None:
            return [file_id for file_id, _path in rows]
        selected = {
            os.path.normcase(os.path.abspath(str(path))) for path in source_paths
        }
        return [
            file_id for file_id, path in rows
            if os.path.normcase(os.path.abspath(path)) in selected
        ]

    def _lookup_page(
        self,
        keys: set[int],
        page_size: int,
        cursor: int | None,
        convert: Callable[[tuple], CompactDictionaryCandidate | None],
    ) -> CompactLookupPage:
        position = max(-1, int(cursor)) if cursor is not None else -1
        scan_size = max(500, page_size * 25)
        accepted: list[tuple[CompactDictionaryCandidate, int]] = []
        seen: set[tuple[str, str, str]] = set()
        exhausted = False
        with closing(self._connect_readonly()) as db:
            file_ids = [int(row[0]) for row in db.execute("SELECT file_id FROM files")]
            while len(accepted) <= page_size:
                rows = _seek_lookup_rows(db, file_ids, keys, position, scan_size)
                if not rows:
                    exhausted = True
                    break
                records = _resolve_records(db, rows)
                for record in records:
                    position = max(position, int(record[-1]))
                    candidate = convert(record)
                    if candidate is None:
                        continue
                    identity = (candidate.text, candidate.code, candidate.source)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    accepted.append((candidate, int(record[-1])))
                    if len(accepted) > page_size:
                        break
                if len(accepted) > page_size:
                    break
                if len(rows) < scan_size:
                    exhausted = True
                    break
                position = max(position, max(int(row[0]) for row in rows))

        page = accepted[:page_size]
        next_cursor = None
        if len(accepted) > page_size:
            next_cursor = page[-1][1]
        elif not exhausted and page:
            next_cursor = page[-1][1]
        return CompactLookupPage([item[0] for item in page], next_cursor)

    def is_current(self, sources: Iterable[CompactDictionarySource]) -> bool:
        if not self._is_compatible():
            return False
        expected = _manifest(_normalise_sources(sources))
        with closing(self._connect_readonly()) as db:
            return _metadata_value(db, "manifest", "") == expected

    def _is_compatible(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with closing(self._connect_readonly()) as db:
                return (
                    _metadata_value(db, "schema_version", "") == str(_SCHEMA_VERSION)
                    and _metadata_value(db, "scheme_id", "") == self.scheme_id
                )
        except sqlite3.Error:
            return False

    def _connect_readonly(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True)

    def _report(self, elapsed: float, full_rebuild: bool) -> CompactIndexReport:
        with closing(self._connect_readonly()) as db:
            file_count = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            entry_count = db.execute("SELECT COALESCE(SUM(entry_count), 0) FROM files").fetchone()[0]
            text_count = entry_count
            code_count = entry_count
        return CompactIndexReport(
            self.path, elapsed, self.path.stat().st_size, file_count,
            entry_count, text_count, code_count, full_rebuild,
        )


def discover_dictionary_sources(
    rime_dir: str | Path,
    roots: Sequence[tuple[str, float]],
) -> list[CompactDictionarySource]:
    """Resolve root dictionaries and import_tables without writing to Rime."""
    directory = Path(rime_dir).resolve()
    result: list[CompactDictionarySource] = []
    seen: set[Path] = set()

    def visit(path: Path, source: str, quality: float) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        try:
            resolved.relative_to(directory)
        except ValueError:
            return
        seen.add(resolved)
        result.append(CompactDictionarySource(resolved, source, quality))
        try:
            lines = resolved.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return
        in_imports = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import_tables:"):
                in_imports = True
                continue
            if in_imports and stripped.startswith("-"):
                target = stripped[1:].split("#", 1)[0].strip().strip("\"'")
                if target:
                    child = directory / target
                    if not child.name.endswith(".dict.yaml"):
                        child = child.with_name(child.name + ".dict.yaml")
                    visit(child, source, quality)
                continue
            if in_imports and stripped and not line[:1].isspace():
                in_imports = False

    for root, quality in roots:
        name = root.removesuffix(".dict.yaml")
        visit(directory / f"{name}.dict.yaml", name, quality)
    return result


def _configure_builder(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA journal_mode = OFF")
    db.execute("PRAGMA synchronous = OFF")
    db.execute("PRAGMA temp_store = MEMORY")
    db.execute("PRAGMA cache_size = -65536")
    db.execute("PRAGMA foreign_keys = ON")


def _create_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE files (
            file_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            quality REAL NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            entry_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE blocks (
            block_id INTEGER PRIMARY KEY,
            file_id INTEGER NOT NULL REFERENCES files(file_id) ON DELETE CASCADE,
            payload BLOB NOT NULL
        );
        CREATE TABLE lookups (
            file_id INTEGER NOT NULL REFERENCES files(file_id),
            lookup_key INTEGER NOT NULL,
            record_id INTEGER NOT NULL,
            PRIMARY KEY(file_id, lookup_key, record_id)
        ) WITHOUT ROWID;
        """
    )


def _create_indexes(db: sqlite3.Connection) -> None:
    # The lookup table primary key is the only search B-Tree required.
    return None


def _insert_file(db: sqlite3.Connection, source: CompactDictionarySource) -> int:
    stat = source.path.stat()
    cursor = db.execute(
        "INSERT INTO files(path, source, quality, size, mtime_ns) VALUES (?, ?, ?, ?, ?)",
        (str(source.path), source.source, source.quality, stat.st_size, stat.st_mtime_ns),
    )
    return int(cursor.lastrowid)


def _insert_source_entries(
    db: sqlite3.Connection,
    file_id: int,
    path: Path,
    splitter: Callable[[str], list[str]] | None,
    *,
    should_cancel: CancelCallback | None = None,
    progress: ProgressCallback | None = None,
    files_completed: int = 0,
    files_total: int = 0,
    processed_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    batch: list[tuple[str, str, int | None, int, str | None, bytes | None]] = []
    entry_count = 0
    current_file_bytes = 0
    for entry, current_file_bytes in _read_entries(path, splitter):
        batch.append(entry)
        entry_count += 1
        if len(batch) >= _BATCH_SIZE:
            _check_cancelled(should_cancel)
            _insert_batch(db, file_id, batch)
            batch.clear()
            if entry_count % (_BATCH_SIZE * 16) == 0:
                _notify_progress(
                    progress, "building", files_completed, files_total,
                    path.name, current_entries=entry_count,
                    processed_bytes=processed_bytes + current_file_bytes,
                    total_bytes=total_bytes,
                )
    if batch:
        _insert_batch(db, file_id, batch)
    db.execute(
        "UPDATE files SET entry_count=? WHERE file_id=?", (entry_count, file_id)
    )


def _seek_lookup_rows(
    db: sqlite3.Connection,
    file_ids: Sequence[int],
    keys: set[int],
    cursor: int,
    limit: int,
) -> list[tuple[int, str, float]]:
    if not file_ids or not keys:
        return []
    file_placeholders = ",".join("?" for _ in file_ids)
    key_values = sorted(keys)
    key_placeholders = ",".join("?" for _ in key_values)
    return db.execute(
        "SELECT DISTINCT l.record_id, f.source, f.quality "
        "FROM lookups l JOIN files f ON f.file_id=l.file_id "
        f"WHERE l.file_id IN ({file_placeholders}) "
        f"AND l.lookup_key IN ({key_placeholders}) AND l.record_id>? "
        "ORDER BY l.record_id LIMIT ?",
        (*file_ids, *key_values, cursor, limit),
    ).fetchall()


def _check_cancelled(should_cancel: CancelCallback | None) -> None:
    if should_cancel is not None and should_cancel():
        raise CompactIndexBuildCancelled("索引构建已取消")


def _notify_progress(
    callback: ProgressCallback | None,
    phase: str,
    files_completed: int,
    files_total: int,
    current_file: str,
    *,
    current_entries: int = 0,
    processed_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    if callback is not None:
        callback({
            "phase": phase,
            "files_completed": files_completed,
            "files_total": files_total,
            "current_file": current_file,
            "current_entries": current_entries,
            "processed_bytes": processed_bytes,
            "total_bytes": total_bytes,
        })


def _copy_with_cancellation(
    source: Path,
    target: Path,
    should_cancel: CancelCallback | None,
) -> None:
    with source.open("rb") as reader, target.open("wb") as writer:
        while True:
            _check_cancelled(should_cancel)
            chunk = reader.read(4 * 1024 * 1024)
            if not chunk:
                break
            writer.write(chunk)
    shutil.copystat(source, target)


def _insert_batch(
    db: sqlite3.Connection,
    file_id: int,
    rows: list[tuple[str, str, int | None, int, str | None, bytes | None]],
) -> None:
    payload = json.dumps(
        [
            [text, code, list(unit_lengths or b""), weight]
            for text, code, weight, _ordinal, _strict, unit_lengths in rows
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    cursor = db.execute(
        "INSERT INTO blocks(file_id, payload) VALUES (?, ?)",
        (file_id, sqlite3.Binary(zlib.compress(payload, level=6))),
    )
    block_id = int(cursor.lastrowid)
    lookups = []
    for offset, (text, code, weight, ordinal, strict, unit_lengths) in enumerate(rows):
        record_id = (block_id << 8) | offset
        packed_text = _pack_text(text)
        packed_code = _pack_code(code)
        lookups.append((file_id, _fingerprint(packed_text, 1), record_id))
        lookups.append((file_id, _fingerprint(packed_code, 2), record_id))
        if strict:
            lookups.append((file_id, _fingerprint(_pack_code(strict), 3), record_id))
    db.executemany(
        "INSERT OR IGNORE INTO lookups(file_id, lookup_key, record_id) VALUES (?, ?, ?)",
        lookups,
    )


def _delete_file_data(db: sqlite3.Connection, file_id: int) -> None:
    db.execute("DELETE FROM lookups WHERE file_id=?", (file_id,))
    db.execute("DELETE FROM blocks WHERE file_id=?", (file_id,))


def _resolve_records(
    db: sqlite3.Connection,
    rows: Sequence[tuple[int, str, float]],
) -> list[tuple[str, str, bytes | None, int | None, str, float, int]]:
    block_ids = sorted({int(record_id) >> 8 for record_id, _source, _quality in rows})
    blocks: dict[int, list] = {}
    for start in range(0, len(block_ids), 800):
        chunk = block_ids[start:start + 800]
        placeholders = ",".join("?" for _ in chunk)
        for block_id, payload in db.execute(
            f"SELECT block_id, payload FROM blocks WHERE block_id IN ({placeholders})",
            chunk,
        ):
            blocks[int(block_id)] = json.loads(
                zlib.decompress(payload).decode("utf-8")
            )
    result = []
    for record_id, source, quality in rows:
        block_id = int(record_id) >> 8
        offset = int(record_id) & 0xFF
        records = blocks.get(block_id)
        if records is None or offset >= len(records):
            continue
        text, code, lengths, weight = records[offset]
        result.append((
            str(text), str(code), bytes(lengths) if lengths else None,
            int(weight) if weight is not None else None,
            source, float(quality), int(record_id),
        ))
    return result


def _read_entries(
    path: Path,
    splitter: Callable[[str], list[str]] | None,
) -> Iterator[tuple[
    tuple[str, str, int | None, int, str | None, bytes | None], int,
]]:
    # Binary iteration exposes an exact byte offset for progress without a
    # second pass or an estimate based on uneven dictionary file counts.
    with path.open("rb") as handle:
        in_data = False
        ordinal = 0
        for raw_line in handle:
            processed_bytes = handle.tell()
            line = raw_line.decode("utf-8", "ignore")
            if line.strip() == "...":
                in_data = True
                continue
            if not in_data or not line or line.lstrip().startswith("#"):
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 2:
                continue
            text = parts[0].strip()
            original_code = parts[1].strip()
            code = _normalise_code(original_code)
            if not text or not code:
                continue
            try:
                weight = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip() else None
            except ValueError:
                weight = None
            ordinal += 1
            strict, unit_lengths = _short_data(
                text, original_code, code, splitter,
            )
            yield (
                text, code, weight, ordinal, strict, unit_lengths,
            ), processed_bytes


def _short_data(
    text: str,
    original_code: str,
    normalized_code: str,
    splitter: Callable[[str], list[str]] | None,
) -> tuple[str | None, bytes | None]:
    if not normalized_code.isascii() or not normalized_code.isalpha():
        return None, None
    if len(text) == 1:
        return normalized_code[:1], bytes((len(normalized_code),))
    if text.isascii() or len(text) > _MAX_SHORTHAND_TEXT_LENGTH:
        return None, None
    raw_units = [part for part in re.split(r"[\s'’`]+", original_code.strip()) if part]
    units = [_normalise_code(part) for part in raw_units]
    units = [part for part in units if part]
    if len(units) < 2 and splitter is not None:
        units = [_normalise_code(part) for part in splitter(normalized_code)]
        units = [part for part in units if part]
    if len(units) < 2 or "".join(units) != normalized_code:
        return None, None
    strict = "".join(unit[0] for unit in units)
    lengths = bytes(len(unit) for unit in units)
    return strict, lengths


def _strict_query_keys(
    value: str,
    splitter: Callable[[str], list[str]] | None,
) -> set[str]:
    """Return exact strict-index buckets that can produce ``value``."""
    keys = {value}
    if len(value) < 2:
        return keys
    # Compact form: full first syllable + initials of the remaining syllables.
    # Trying legal pinyin syllable lengths only broadens the indexed buckets;
    # candidates are still verified against their stored boundaries below.
    for first_length in range(2, min(6, len(value) - 1) + 1):
        keys.add(value[0] + value[first_length:])
    # Mixed form with one tail syllable.
    keys.add(value[:2])
    if splitter is not None:
        tail_units = splitter(value[1:])
        if tail_units:
            keys.add(value[0] + "".join(unit[0] for unit in tail_units if unit))
    return {key for key in keys if key}


def _forms_from_lengths(code: str, encoded_lengths: bytes | None) -> dict[str, str]:
    if not encoded_lengths:
        return {}
    lengths = list(encoded_lengths)
    if not lengths or sum(lengths) != len(code):
        return {}
    units = []
    offset = 0
    for length in lengths:
        units.append(code[offset:offset + length])
        offset += length
    result: dict[str, str] = {}
    strict = "".join(unit[0] for unit in units)
    result.setdefault(strict, "strict")
    if len(units) > 1:
        compact = units[0] + "".join(unit[0] for unit in units[1:])
        mixed = units[0][0] + "".join(units[1:])
        result.setdefault(compact, "compact")
        result.setdefault(mixed, "mixed")
    return result


def _candidate_from_row(row, match_kind: str) -> CompactDictionaryCandidate:
    return CompactDictionaryCandidate(
        _unpack_text(row[0]), _unpack_code(row[1]), row[3], row[4], row[5], match_kind,
    )


def _pack_code(code: str) -> bytes:
    """Losslessly pack common ASCII letter codes into five bits per letter."""
    if not code or len(code) > 255 or not code.isascii() or not code.isalpha():
        return b"\xff" + code.encode("utf-8")
    output = bytearray((0, len(code)))
    buffer = 0
    bit_count = 0
    for char in code:
        buffer = (buffer << 5) | (ord(char) - 96)
        bit_count += 5
        while bit_count >= 8:
            bit_count -= 8
            output.append((buffer >> bit_count) & 0xFF)
            buffer &= (1 << bit_count) - 1
    if bit_count:
        output.append((buffer << (8 - bit_count)) & 0xFF)
    return bytes(output)


def _pack_text(text: str) -> bytes:
    utf8 = text.encode("utf-8")
    utf16 = text.encode("utf-16le")
    return (b"\x01" + utf16) if len(utf16) < len(utf8) else (b"\x00" + utf8)


def _unpack_text(value: bytes) -> str:
    data = bytes(value)
    if not data:
        return ""
    if data[0] == 1:
        return data[1:].decode("utf-16le")
    if data[0] == 0:
        return data[1:].decode("utf-8")
    raise ValueError("invalid packed dictionary text")


def _fingerprint(value: bytes, domain: int) -> int:
    person = b"RimeIdx4" + bytes((domain & 0xFF,))
    digest = hashlib.blake2b(value, digest_size=8, person=person).digest()
    return int.from_bytes(digest, "big", signed=True)


def _unpack_code(value: bytes) -> str:
    data = bytes(value)
    if not data:
        return ""
    if data[0] == 0xFF:
        return data[1:].decode("utf-8")
    if len(data) < 2 or data[0] != 0:
        raise ValueError("invalid packed dictionary code")
    length = data[1]
    buffer = 0
    bit_count = 0
    result = []
    for byte in data[2:]:
        buffer = (buffer << 8) | byte
        bit_count += 8
        while bit_count >= 5 and len(result) < length:
            bit_count -= 5
            number = (buffer >> bit_count) & 0x1F
            if not 1 <= number <= 26:
                raise ValueError("invalid packed dictionary code")
            result.append(chr(96 + number))
            buffer &= (1 << bit_count) - 1
    if len(result) != length:
        raise ValueError("truncated packed dictionary code")
    return "".join(result)


def _normalise_code(code: str) -> str:
    value = (code or "").lower().translate(str.maketrans({
        "ü": "v", "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v",
    }))
    value = "".join(
        char for char in unicodedata.normalize("NFD", value)
        if not unicodedata.combining(char)
    )
    return raw_code(value).replace(" ", "")


def _normalise_sources(
    sources: Iterable[CompactDictionarySource],
) -> list[CompactDictionarySource]:
    unique: dict[Path, CompactDictionarySource] = {}
    for source in sources:
        resolved = Path(source.path).resolve()
        if resolved.is_file():
            unique.setdefault(
                resolved,
                CompactDictionarySource(resolved, source.source, float(source.quality)),
            )
    return list(unique.values())


def _manifest(sources: Sequence[CompactDictionarySource]) -> str:
    values = []
    for source in sources:
        stat = source.path.stat()
        values.append((
            str(source.path), stat.st_size, stat.st_mtime_ns,
            source.source, source.quality,
        ))
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def _write_manifest(
    db: sqlite3.Connection,
    sources: Sequence[CompactDictionarySource],
    incremental_updates: int,
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('manifest', ?)",
        (_manifest(sources),),
    )
    db.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES ('incremental_updates', ?)",
        (str(incremental_updates),),
    )


def _metadata_value(db: sqlite3.Connection, key: str, default: str) -> str:
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return str(row[0]) if row else default


def _safe_scheme_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("_.")
