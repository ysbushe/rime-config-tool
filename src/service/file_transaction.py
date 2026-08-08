"""Recoverable multi-file writes for Rime-managed data.

Individual files are replaced atomically.  A small journal keeps the original
bytes until every replacement succeeds, allowing rollback after an exception or
recovery after an interrupted process.  This is intentionally independent from
the current UI so VNext editors can share one write boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4

from src.utils.encoding import read_text_utf8, write_text_utf8


@dataclass(frozen=True)
class FileRevision:
    exists: bool
    size: int
    mtime_ns: int
    sha256: str

    @classmethod
    def missing(cls) -> "FileRevision":
        return cls(False, 0, 0, "")


@dataclass(frozen=True)
class FileChange:
    path: Path
    payload: bytes
    expected_revision: FileRevision | None = None


@dataclass(frozen=True)
class FileTransactionReport:
    transaction_id: str
    changed_paths: tuple[Path, ...]


class FileTransactionConflict(RuntimeError):
    pass


class FileTransactionError(RuntimeError):
    pass


def capture_revision(path: str | Path) -> FileRevision:
    target = Path(path)
    try:
        stat = target.stat()
        if not target.is_file():
            raise FileTransactionConflict(f"目标不是普通文件：{target}")
        digest = _hash_file(target)
        return FileRevision(True, stat.st_size, stat.st_mtime_ns, digest)
    except FileNotFoundError:
        return FileRevision.missing()


class TransactionalFileWriter:
    """Commit a set of byte payloads with conflict checks and rollback."""

    def __init__(self, journal_root: str | Path) -> None:
        self.journal_root = Path(journal_root)

    def commit(self, changes: Iterable[FileChange]) -> FileTransactionReport:
        items = _normalise_changes(changes)
        if not items:
            raise ValueError("事务至少需要一个文件变更")
        self._verify_expected(items)

        transaction_id = uuid4().hex
        journal = self.journal_root / transaction_id
        journal.mkdir(parents=True, exist_ok=False)
        entries: list[dict] = []
        try:
            for index, change in enumerate(items):
                change.path.parent.mkdir(parents=True, exist_ok=True)
                staged = _stage_payload(change.path, change.payload, transaction_id)
                revision = capture_revision(change.path)
                backup = journal / f"{index:04d}.bak"
                if revision.exists:
                    shutil.copy2(change.path, backup)
                entries.append({
                    "target": str(change.path),
                    "staged": str(staged),
                    "backup": str(backup),
                    "original": asdict(revision),
                    "payload_sha256": hashlib.sha256(change.payload).hexdigest(),
                })

            manifest = journal / "manifest.json"
            _write_manifest(manifest, transaction_id, "prepared", entries)
            self._verify_expected(items)
            for index, entry in enumerate(entries):
                _write_manifest(
                    manifest, transaction_id, "applying", entries,
                    applying_index=index,
                )
                _replace_staged(Path(entry["staged"]), Path(entry["target"]))
            _write_manifest(manifest, transaction_id, "committed", entries)
        except Exception as exc:
            rollback_errors = _restore_entries(entries)
            if rollback_errors:
                raise FileTransactionError(
                    f"事务写入失败且回滚不完整：{exc}；{'；'.join(rollback_errors)}"
                ) from exc
            _remove_journal(journal)
            raise

        _remove_journal(journal)
        return FileTransactionReport(
            transaction_id, tuple(change.path for change in items),
        )

    def recover_pending(self) -> list[str]:
        """Restore all incomplete journals; safe to call once during startup."""
        if not self.journal_root.is_dir():
            return []
        recovered = []
        for journal in sorted(self.journal_root.iterdir()):
            manifest = journal / "manifest.json"
            if not journal.is_dir() or not manifest.is_file():
                continue
            try:
                value = json.loads(read_text_utf8(manifest))
                state = str(value.get("state", ""))
                if state == "committed":
                    _remove_journal(journal)
                    continue
                errors = _restore_entries(list(value.get("entries") or []))
                if errors:
                    raise FileTransactionError("；".join(errors))
                recovered.append(str(value.get("transaction_id") or journal.name))
                _remove_journal(journal)
            except (OSError, ValueError, json.JSONDecodeError):
                # Keep an unreadable journal intact for manual diagnosis.
                continue
        return recovered

    @staticmethod
    def _verify_expected(changes: list[FileChange]) -> None:
        for change in changes:
            if change.expected_revision is None:
                continue
            actual = capture_revision(change.path)
            if actual != change.expected_revision:
                raise FileTransactionConflict(
                    f"文件已被其他程序修改，本次未写入：{change.path}"
                )


def _normalise_changes(changes: Iterable[FileChange]) -> list[FileChange]:
    result = []
    seen: set[Path] = set()
    for change in changes:
        path = Path(change.path).resolve()
        if path in seen:
            raise ValueError(f"同一事务不能重复写入文件：{path}")
        seen.add(path)
        result.append(FileChange(path, bytes(change.payload), change.expected_revision))
    return result


def _stage_payload(target: Path, payload: bytes, transaction_id: str) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f".{target.name}.{transaction_id}.", suffix=".tmp", dir=target.parent,
    )
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _replace_staged(staged: Path, target: Path) -> None:
    os.replace(staged, target)


def _restore_entries(entries: list[dict]) -> list[str]:
    errors = []
    for entry in reversed(entries):
        target = Path(str(entry["target"]))
        staged = Path(str(entry["staged"]))
        backup = Path(str(entry["backup"]))
        original = dict(entry.get("original") or {})
        try:
            if bool(original.get("exists")) and backup.is_file():
                restored = _stage_payload(
                    target, backup.read_bytes(), f"restore-{uuid4().hex}",
                )
                os.replace(restored, target)
            elif target.is_file() and _hash_file(target) == entry.get("payload_sha256"):
                target.unlink()
            staged.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"恢复 {target} 失败：{exc}")
    return errors


def _write_manifest(
    path: Path,
    transaction_id: str,
    state: str,
    entries: list[dict],
    *,
    applying_index: int | None = None,
) -> None:
    write_text_utf8(path, json.dumps({
        "version": 1,
        "transaction_id": transaction_id,
        "state": state,
        "applying_index": applying_index,
        "entries": entries,
    }, ensure_ascii=False, indent=2))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_journal(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
