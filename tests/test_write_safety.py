"""写入安全回归测试：解析失败不写回、保存前备份、自动部署开关一致。"""
from __future__ import annotations

import pytest

from src.repo.phrase_repo import PhraseRepo
from src.repo.schema_repo import SchemaRepo
from src.repo.symbols_repo import SymbolsRepo
from src.service.backup_service import BackupService
from src.service.deploy_service import DeployService
from src.service.group_service import GroupService
from src.ui.phrase_manager import PhraseManager
from src.ui.phrase_table import COL_CODE, COL_WEIGHT


class _Settings:
    sandbox_mode = False
    auto_deploy = False
    auto_group_done = True


class _Deploy:
    def __init__(self) -> None:
        self.calls = 0

    def deploy(self) -> tuple[bool, str]:
        self.calls += 1
        return True, "ok"


def test_schema_parse_error_blocks_save(tmp_path) -> None:
    path = tmp_path / "rime_frost.schema.yaml"
    original = "schema: [broken\n"
    path.write_text(original, encoding="utf-8")

    repo = SchemaRepo(str(path))

    assert repo.load_error
    with pytest.raises(RuntimeError):
        repo.save()
    assert path.read_text(encoding="utf-8") == original


def test_symbols_parse_error_blocks_save(tmp_path) -> None:
    path = tmp_path / "symbols_v.yaml"
    original = "symbols: [broken\n"
    path.write_text(original, encoding="utf-8")

    repo = SymbolsRepo(str(path))

    assert repo.load_error
    with pytest.raises(RuntimeError):
        repo.save()
    assert path.read_text(encoding="utf-8") == original


def test_quick_add_resets_view_and_selects_new_row(qapp, temp_rime_dir) -> None:
    settings = _Settings()
    manager = PhraseManager(
        PhraseRepo(str(temp_rime_dir / "custom_phrase.txt")),
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        settings,
        _Deploy(),
    )
    manager._keyword = "不存在"
    manager._current_group = "不存在的分组"
    manager._sort_combo.setCurrentText("权重")

    manager.quick_add("新采集词", "xincaijici", weight=2)

    current = manager._table.currentIndex()
    assert current.isValid()
    assert manager._table.model().key_at_row(current.row()) == "新采集词\txincaijici"
    assert current.row() == 0
    assert manager._keyword == ""
    assert manager._sort_combo.currentText() == "加入顺序倒序"


def test_phrase_inline_save_backs_up_and_respects_auto_deploy(qapp, temp_rime_dir) -> None:
    settings = _Settings()
    deploy = _Deploy()
    backup = BackupService(str(temp_rime_dir), keep=5)
    manager = PhraseManager(
        PhraseRepo(str(temp_rime_dir / "custom_phrase.txt")),
        GroupService(str(temp_rime_dir)),
        backup,
        settings,
        deploy,
    )

    manager._on_cell_edited(0, COL_WEIGHT, "321")
    manager._on_save()

    assert backup.list_backups("custom_phrase.txt")
    assert deploy.calls == 0


def test_phrase_code_edit_stores_raw_code_and_display_boundary(
        qapp, temp_rime_dir) -> None:
    manager = PhraseManager(
        PhraseRepo(str(temp_rime_dir / "custom_phrase.txt")),
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )
    phrase = manager._displayed[0]

    manager._on_cell_edited(0, COL_CODE, "abc’def")

    assert phrase.code == "abcdef"
    assert manager._display_store.display_for(phrase) == "abc'def"


def test_phrase_backup_includes_display_sidecar(qapp, temp_rime_dir) -> None:
    display_ini = temp_rime_dir / "pinyin_display.ini"
    display_ini.write_text("[display]\n", encoding="utf-8")
    backup = BackupService(str(temp_rime_dir), keep=5)
    manager = PhraseManager(
        PhraseRepo(str(temp_rime_dir / "custom_phrase.txt")),
        GroupService(str(temp_rime_dir)),
        backup,
        _Settings(),
        _Deploy(),
    )

    manager._persist_backup()

    assert backup.list_backups("custom_phrase.txt")
    assert backup.list_backups("pinyin_display.ini")


def test_exact_duplicate_keeps_weight_without_explicit_conflict_edit(
        qapp, temp_rime_dir, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.ui.phrase_manager.QMessageBox.information", lambda *args: None)
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    existing.weight = 23
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_upsert(
        existing.text, existing.code, 1, "", is_new=True,
        display_code=existing.code,
    )

    assert repo.find(existing.text, existing.code).weight == 23


def test_exact_duplicate_applies_explicit_conflict_weight(
        qapp, temp_rime_dir, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.ui.phrase_manager.QMessageBox.information", lambda *args: None)
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_upsert(
        existing.text, existing.code, 1, "", is_new=True,
        display_code=existing.code,
        weight_updates={existing.key: 37},
    )

    assert repo.find(existing.text, existing.code).weight == 37


def test_quick_capture_preserves_existing_weight_when_not_explicitly_changed(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    existing.weight = 23
    repo.save()
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_multiple_codes({
        "text": existing.text,
        "code": existing.code,
        "display_code": existing.code,
        "additional_codes": [],
        "weight": 1,
        "weight_explicit": False,
        "weight_updates": {},
        "group": "",
        "group_explicit": False,
    })

    assert repo.find(existing.text, existing.code).weight == 23


def test_quick_capture_applies_conflict_weight_without_default_overwrite(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_multiple_codes({
        "text": existing.text,
        "code": existing.code,
        "display_code": existing.code,
        "additional_codes": [],
        "weight": 1,
        "weight_explicit": False,
        "weight_updates": {existing.key: 37},
        "group": "",
        "group_explicit": False,
    })

    assert repo.find(existing.text, existing.code).weight == 37


def test_quick_capture_applies_explicit_primary_weight(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_multiple_codes({
        "text": existing.text,
        "code": existing.code,
        "display_code": existing.code,
        "additional_codes": [],
        "weight": 12,
        "weight_explicit": True,
        "weight_updates": {},
        "group": "",
        "group_explicit": False,
    })

    assert repo.find(existing.text, existing.code).weight == 12


def test_quick_capture_preserves_existing_group_when_not_explicitly_changed(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    existing = repo.all()[0]
    groups = GroupService(str(temp_rime_dir))
    groups.set_entry_group(existing.text, existing.code, "工作")
    manager = PhraseManager(
        repo,
        groups,
        BackupService(str(temp_rime_dir), keep=5),
        _Settings(),
        _Deploy(),
    )

    manager._apply_multiple_codes({
        "text": existing.text,
        "code": existing.code,
        "display_code": existing.code,
        "additional_codes": [],
        "weight": 1,
        "weight_explicit": False,
        "weight_updates": {},
        "group": "",
        "group_explicit": False,
    })

    assert groups.get_entry_group(existing.text, existing.code) == "工作"


def test_quick_tune_rank_action_is_backed_up_and_saved(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    repo.upsert("候选甲", "tongma", 9)
    phrase, _new, _conflict = repo.upsert("候选乙", "tongma", 2)
    repo.save()
    backup = BackupService(str(temp_rime_dir), keep=5)
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        backup,
        _Settings(),
        _Deploy(),
    )

    draft = repo.clone_in_memory()
    assert draft.adjust_rank(phrase.text, phrase.code, "top")
    stat = repo.path.stat()
    changed, message = manager._save_quick_tune_changes(
        draft.all(), (stat.st_size, stat.st_mtime_ns)
    )

    assert changed is True
    saved = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    assert saved.candidates_for_code("tongma")[0].text == "候选乙"
    assert "全部修改已统一保存" in message
    assert backup.list_backups("custom_phrase.txt")


def test_quick_tune_system_word_is_added_as_personal_override(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    repo.save()
    backup = BackupService(str(temp_rime_dir), keep=5)
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        backup,
        _Settings(),
        _Deploy(),
    )

    draft = repo.clone_in_memory()
    draft.upsert("了", "le", 99)
    stat = repo.path.stat()
    changed, message = manager._save_quick_tune_changes(
        draft.all(), (stat.st_size, stat.st_mtime_ns)
    )

    assert changed is True
    saved = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    phrase = saved.find("了", "le")
    assert phrase is not None
    assert phrase.weight == 99
    assert "全部修改已统一保存" in message
    assert backup.list_backups("custom_phrase.txt")


def test_quick_tune_save_rejects_external_file_change(
        qapp, temp_rime_dir) -> None:
    repo = PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))
    backup = BackupService(str(temp_rime_dir), keep=5)
    manager = PhraseManager(
        repo,
        GroupService(str(temp_rime_dir)),
        backup,
        _Settings(),
        _Deploy(),
    )
    stat = repo.path.stat()
    expected = (stat.st_size, stat.st_mtime_ns)
    draft = repo.clone_in_memory()
    draft.upsert("外部冲突", "waibuchongtu", 9)
    repo.path.write_text(
        repo.path.read_text(encoding="utf-8") + "外部写入\tw\t1\n",
        encoding="utf-8",
    )

    changed, message = manager._save_quick_tune_changes(
        draft.all(), expected
    )

    assert changed is False
    assert "其他程序修改" in message
    assert "外部写入\tw\t1" in repo.path.read_text(encoding="utf-8")
    assert not backup.list_backups("custom_phrase.txt")


class _SandboxSettings:
    sandbox_mode = True
    rime_dir = ""
    deployer_path = ""


def test_deploy_service_blocks_real_deploy_in_sandbox(monkeypatch) -> None:
    monkeypatch.setattr(DeployService, "_detect", lambda self: "C:/Rime/WeaselDeployer.exe")
    deploy = DeployService(_SandboxSettings())
    calls = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: calls.append((args, kwargs)))

    ok, msg = deploy.deploy()

    assert ok is False
    assert "沙盒模式" in msg
    assert calls == []


def test_deploy_service_waits_for_stable_weasel_server(
        monkeypatch, tmp_path) -> None:
    import subprocess

    class _Settings:
        sandbox_mode = False
        rime_dir = ""
        deployer_path = ""

    deployer = tmp_path / "WeaselDeployer.exe"
    deployer.touch()
    deployer.with_name("WeaselServer.exe").touch()
    monkeypatch.setattr(DeployService, "_detect", lambda self: str(deployer))
    states = iter((False, True, True, True))
    monkeypatch.setattr(
        DeployService, "_weasel_server_running",
        staticmethod(lambda: next(states)),
    )
    monkeypatch.setattr("src.service.deploy_service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "src.service.deploy_service.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    ok, message = DeployService(_Settings()).deploy()

    assert ok is True
    assert "算法服务已恢复" in message


def test_deploy_service_reports_unverifiable_server_state(
        monkeypatch, tmp_path) -> None:
    import subprocess

    class _Settings:
        sandbox_mode = False
        rime_dir = ""
        deployer_path = ""

    deployer = tmp_path / "WeaselDeployer.exe"
    deployer.touch()
    deployer.with_name("WeaselServer.exe").touch()
    monkeypatch.setattr(DeployService, "_detect", lambda self: str(deployer))
    monkeypatch.setattr(
        DeployService, "_weasel_server_running", staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        "src.service.deploy_service.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )

    ok, message = DeployService(_Settings()).deploy()

    assert ok is False
    assert "无法确认算法服务状态" in message


def test_phrase_bundle_rolls_back_all_files_when_second_replace_fails(
        qapp, temp_rime_dir, tmp_path, monkeypatch) -> None:
    from src.service.file_transaction import TransactionalFileWriter
    from src.service import file_transaction

    phrase_path = temp_rime_dir / "custom_phrase.txt"
    display_path = temp_rime_dir / "pinyin_display.ini"
    display_path.write_text("[display]\n", encoding="utf-8")
    groups = GroupService(str(temp_rime_dir))
    groups.add_group("工作")
    group_path = groups.path
    originals = {
        phrase_path: phrase_path.read_bytes(),
        display_path: display_path.read_bytes(),
        group_path: group_path.read_bytes(),
    }
    repo = PhraseRepo(str(phrase_path))
    manager = PhraseManager(
        repo, groups, BackupService(str(temp_rime_dir), keep=5),
        _Settings(), _Deploy(),
        transaction_writer=TransactionalFileWriter(tmp_path / "transactions"),
    )
    real_replace = file_transaction._replace_staged
    calls = 0

    def fail_second(staged, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("locked sidecar")
        real_replace(staged, target)

    monkeypatch.setattr(file_transaction, "_replace_staged", fail_second)
    with pytest.raises(OSError, match="locked sidecar"):
        manager._apply_multiple_codes({
            "text": "事务测试", "code": "shiwuceshi", "display_code": "shi'wu'ce'shi",
            "additional_codes": [], "weight": 5, "weight_explicit": True,
            "weight_updates": {}, "group": "工作", "group_explicit": True,
        })

    assert all(path.read_bytes() == payload for path, payload in originals.items())
    # Disk rollback and editable UI state are separate guarantees: failed
    # writes restore every file but keep the staged values available to retry.
    assert repo.find("事务测试", "shiwuceshi") is not None
    assert groups.get_entry_group("事务测试", "shiwuceshi") == "工作"
    assert manager.has_unsaved_changes() is True

    manager._on_save()

    assert manager.has_unsaved_changes() is False
    assert "事务测试".encode("utf-8") in phrase_path.read_bytes()
