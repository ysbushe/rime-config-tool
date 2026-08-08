from __future__ import annotations

from pathlib import Path
from threading import Event
import time
from unittest.mock import MagicMock

from src.service.deploy_coordinator import AsyncDeployCoordinator
from src.service.dictionary_catalog import DictionaryCatalogItem
from src.service.dictionary_edit_session import DictionaryEditSession
from src.service.file_transaction import (
    FileTransactionConflict,
    TransactionalFileWriter,
)
from src.service.rime_profiles import scheme_dictionary_roots, scheme_title
from src.ui.index_progress import present_index_progress


def test_scheme_registry_is_shared_by_catalog_and_index_adapter() -> None:
    assert scheme_title("wanxiang") == "万象拼音"
    assert scheme_dictionary_roots("wanxiang") == (
        ("wanxiang", 1.2),
        ("wanxiang_english", 1.1),
        ("wanxiang_mixedcode", 1.0),
        ("wanxiang_reverse", 0.9),
    )


def test_index_progress_presentation_handles_bytes_phases_and_bad_values() -> None:
    building = present_index_progress({
        "phase": "building", "processed_bytes": 40, "total_bytes": 100,
        "files_completed": 2, "files_total": 5, "current_file": "base.dict.yaml",
    })
    switching = present_index_progress({"phase": "switching"})
    malformed = present_index_progress({"processed_bytes": "bad", "files_total": None})

    assert building.percent == 38
    assert building.phase_label == "生成"
    assert building.current_file == "base.dict.yaml"
    assert switching.percent == 99
    assert malformed.percent == 0


def test_dictionary_edit_session_keeps_staged_state_after_external_conflict(
        tmp_path: Path) -> None:
    path = tmp_path / "personal.txt"
    path.write_text("旧词\tjiuci\t1\n", encoding="utf-8")
    item = DictionaryCatalogItem(
        "wanxiang", "个人词典", path.name, "测试个人词典",
        "个人文本词典", path, (path,), True,
    )
    session = DictionaryEditSession(
        item, TransactionalFileWriter(tmp_path / "transactions"),
    )
    assert session.update(0, 0, "暂存词") == ""
    path.write_text("外部词\twaibuci\t2\n", encoding="utf-8")

    try:
        session.save()
    except FileTransactionConflict:
        pass
    else:
        raise AssertionError("external edit must reject the staged save")

    assert session.dirty
    assert session.entries[0].text == "暂存词"
    assert path.read_text(encoding="utf-8") == "外部词\twaibuci\t2\n"


def test_deploy_coordinator_coalesces_repeated_requests() -> None:
    entered = Event()
    release = Event()
    deploy = MagicMock()

    def run():
        entered.set()
        release.wait(1)
        return True, "ok"

    deploy.deploy.side_effect = run
    coordinator = AsyncDeployCoordinator(deploy)

    assert coordinator.request()
    assert entered.wait(1)
    assert not coordinator.request()
    release.set()
    deadline = time.monotonic() + 1
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = coordinator.take_result()
        if outcome is None:
            time.sleep(0.01)

    assert outcome is not None
    assert outcome.ok
    assert outcome.rerun_requested
    assert deploy.deploy.call_count == 1
