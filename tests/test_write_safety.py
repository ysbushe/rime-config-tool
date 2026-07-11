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
