from __future__ import annotations

import json

import pytest

from src.service.file_transaction import (
    FileChange,
    FileRevision,
    FileTransactionConflict,
    TransactionalFileWriter,
    capture_revision,
)


def test_transaction_commits_multiple_files_and_cleans_journal(tmp_path):
    first = tmp_path / "Rime" / "custom_phrase.txt"
    second = tmp_path / "Rime" / "pinyin_display.ini"
    first.parent.mkdir()
    first.write_bytes(b"old-phrase")
    second.write_bytes(b"old-display")
    writer = TransactionalFileWriter(tmp_path / "journal")

    report = writer.commit([
        FileChange(first, b"new-phrase", capture_revision(first)),
        FileChange(second, b"new-display", capture_revision(second)),
    ])

    assert first.read_bytes() == b"new-phrase"
    assert second.read_bytes() == b"new-display"
    assert report.changed_paths == (first.resolve(), second.resolve())
    assert not list((tmp_path / "journal").iterdir())


def test_transaction_conflict_writes_nothing(tmp_path):
    target = tmp_path / "custom_phrase.txt"
    target.write_bytes(b"current")
    writer = TransactionalFileWriter(tmp_path / "journal")

    with pytest.raises(FileTransactionConflict, match="其他程序修改"):
        writer.commit([
            FileChange(target, b"new", FileRevision.missing()),
        ])

    assert target.read_bytes() == b"current"


def test_transaction_rolls_back_every_file_when_second_replace_fails(
    tmp_path, monkeypatch,
):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first-old")
    second.write_bytes(b"second-old")
    writer = TransactionalFileWriter(tmp_path / "journal")
    from src.service import file_transaction

    real_replace = file_transaction._replace_staged
    calls = 0

    def fail_second(staged, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("locked")
        real_replace(staged, target)

    monkeypatch.setattr(file_transaction, "_replace_staged", fail_second)
    with pytest.raises(OSError, match="locked"):
        writer.commit([
            FileChange(first, b"first-new", capture_revision(first)),
            FileChange(second, b"second-new", capture_revision(second)),
        ])

    assert first.read_bytes() == b"first-old"
    assert second.read_bytes() == b"second-old"
    assert not list((tmp_path / "journal").iterdir())


def test_recover_pending_restores_original_after_interrupted_commit(tmp_path):
    target = tmp_path / "custom_phrase.txt"
    target.write_bytes(b"new")
    journal = tmp_path / "journal" / "tx1"
    journal.mkdir(parents=True)
    backup = journal / "0000.bak"
    backup.write_bytes(b"old")
    staged = tmp_path / ".unused.tmp"
    staged.write_bytes(b"new")
    (journal / "manifest.json").write_text(json.dumps({
        "version": 1,
        "transaction_id": "tx1",
        "state": "applying",
        "entries": [{
            "target": str(target),
            "staged": str(staged),
            "backup": str(backup),
            "original": {"exists": True, "size": 3, "mtime_ns": 1, "sha256": "x"},
            "payload_sha256": __import__("hashlib").sha256(b"new").hexdigest(),
        }],
    }), encoding="utf-8")

    recovered = TransactionalFileWriter(tmp_path / "journal").recover_pending()

    assert recovered == ["tx1"]
    assert target.read_bytes() == b"old"
    assert not journal.exists()
