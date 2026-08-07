from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.service.wanxiang_import import (
    ImportExecutionError,
    PhraseFormatError,
    TARGET_FILENAME,
    TARGET_USER_DICT,
    WanxiangImportService,
    build_wanxiang_patch,
    parse_phrase_bytes,
    scan_compatibility,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rime_dir(tmp_path: Path, source: bytes | None = None) -> Path:
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "custom_phrase.txt").write_bytes(
        source or b"# source\n\xe8\xa5\xbf\xe5\xae\x89\txi'an\n\xe6\xb5\x8b\xe8\xaf\x95\tceshi\t101\n"
    )
    (rime / "wanxiang.schema.yaml").write_text(
        "schema:\n  schema_id: wanxiang\ncustom_phrase:\n  user_dict: custom_phrase\n",
        encoding="utf-8",
    )
    return rime


def test_lossless_document_preserves_apostrophe_columns_weight_bom_and_newline():
    raw = b"\xef\xbb\xbf# head\r\nxi'an\txi'an\r\nweight\tweight\t1000\r\n"
    document = parse_phrase_bytes(raw)

    assert document.render() == raw
    assert document.entries[0].code == "xi'an"
    assert document.entries[0].weight_raw is None
    assert document.entries[1].weight_raw == "1000"
    assert document.entries[1].weight_value == 1000


@pytest.mark.parametrize("raw", [
    b"text,code,1\n",
    b"---\nname: fixed\ncolumns:\n  - text\n...\ntext\tcode\t1\n",
    b"\xff\xfet\x00e\x00x\x00t\x00",
])
def test_strict_type_gate_rejects_csv_dict_yaml_and_utf16(raw: bytes):
    with pytest.raises(PhraseFormatError):
        parse_phrase_bytes(raw)


def test_compatibility_scan_does_not_modify_special_values():
    document = parse_phrase_bytes(
        "西安\txi'an\n日期\\Y\triqi1\tabc\n".encode("utf-8")
    )

    kinds = {warning.kind for warning in scan_compatibility(document.entries)}

    assert {"pinyin_delimiter", "special_code", "dynamic_text", "non_numeric_weight"} <= kinds
    assert document.entries[0].raw == "西安\txi'an"
    assert document.entries[1].weight_raw == "abc"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_patch_is_comment_preserving_and_idempotent(newline: str):
    original = newline.join((
        "patch:",
        "  # keep this comment",
        "  \"custom_phrase/user_dict\": custom_phrase  # local phrases",
        "",
    )).encode()

    patched, conflict = build_wanxiang_patch(original)
    patched_again, conflict_again = build_wanxiang_patch(patched)

    assert conflict is None and conflict_again is None
    assert b"# keep this comment" in patched
    assert b"# local phrases" in patched
    assert TARGET_USER_DICT.encode() in patched
    assert patched_again == patched


def test_plan_is_one_way_and_preserves_target_order(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    source_hash = _sha(rime / "custom_phrase.txt")
    (rime / TARGET_FILENAME).write_text(
        "# target\n已有\tyiyou\t8\n西安\txi'an\t9\n", encoding="utf-8"
    )
    service = WanxiangImportService(rime, tmp_path / "backups", lambda: (True, "ok"))

    plan = service.plan()
    rendered = plan.target_bytes.decode("utf-8")

    assert plan.source_count == 2
    assert plan.import_count == 1
    assert plan.skipped_exact == 1
    assert rendered.index("已有\tyiyou\t8") < rendered.index("测试\tceshi\t101")
    assert _sha(rime / "custom_phrase.txt") == source_hash


def test_existing_non_default_wanxiang_dictionary_blocks_overwrite(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    (rime / "wanxiang.custom.yaml").write_text(
        'patch:\n  "custom_phrase/user_dict": my_private_dict\n', encoding="utf-8"
    )
    service = WanxiangImportService(rime, tmp_path / "backups", lambda: (True, "ok"))

    plan = service.plan()

    assert plan.blockers
    assert "my_private_dict" in plan.blockers[0]


def test_external_change_after_preview_aborts_before_writing(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    service = WanxiangImportService(rime, tmp_path / "backups", lambda: (True, "ok"))
    plan = service.plan()
    (rime / "custom_phrase.txt").write_text("外部修改\twaibu\n", encoding="utf-8")

    with pytest.raises(ImportExecutionError, match="发生变化"):
        service.execute(plan, verify_timeout=0)

    assert not (rime / TARGET_FILENAME).exists()
    assert not (rime / "wanxiang.custom.yaml").exists()


def test_snapshot_failure_aborts_without_changes(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    backup_root = tmp_path / "not-a-directory"
    backup_root.write_text("occupied", encoding="utf-8")
    service = WanxiangImportService(rime, backup_root, lambda: (True, "ok"))
    plan = service.plan()

    with pytest.raises(ImportExecutionError, match="无法创建迁移快照"):
        service.execute(plan, verify_timeout=0)

    assert not (rime / TARGET_FILENAME).exists()
    assert not (rime / "wanxiang.custom.yaml").exists()


def test_failed_deploy_rolls_back_existing_patch_and_new_target(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    original_patch = b"patch:\n  ascii_composer/good_old_caps_lock: true\n"
    (rime / "wanxiang.custom.yaml").write_bytes(original_patch)
    service = WanxiangImportService(
        rime, tmp_path / "backups", lambda: (False, "deploy failed")
    )
    plan = service.plan()

    with pytest.raises(ImportExecutionError, match="deploy failed"):
        service.execute(plan, verify_timeout=0)

    assert not (rime / TARGET_FILENAME).exists()
    assert (rime / "wanxiang.custom.yaml").read_bytes() == original_patch


def test_success_deploy_verifies_built_schema_and_keeps_source_unchanged(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    source_hash = _sha(rime / "custom_phrase.txt")

    def deploy():
        build = rime / "build"
        build.mkdir(exist_ok=True)
        (build / "wanxiang.schema.yaml").write_text(
            f"custom_phrase:\n  user_dict: {TARGET_USER_DICT}\n", encoding="utf-8"
        )
        return True, "deployed"

    service = WanxiangImportService(rime, tmp_path / "backups", deploy)
    result = service.execute(service.plan(), verify_timeout=1)

    assert result.imported == 2
    assert result.snapshot_dir.is_dir()
    assert "西安\txi'an" in (rime / TARGET_FILENAME).read_text(encoding="utf-8-sig")
    assert _sha(rime / "custom_phrase.txt") == source_hash
    assert TARGET_USER_DICT in (rime / "wanxiang.custom.yaml").read_text(encoding="utf-8")


def test_import_dialog_reports_counts_without_phrase_text(qapp, tmp_path: Path):
    from src.ui.wanxiang_import_dialog import WanxiangImportDialog

    rime = _rime_dir(tmp_path)
    service = WanxiangImportService(
        rime, tmp_path / "backups", lambda: (True, "ok")
    )
    dialog = WanxiangImportDialog(service)

    dialog.scan()
    report = dialog._report.toPlainText()

    assert dialog.windowTitle() == "把白霜词库复制到万象拼音"
    assert dialog._source_value.text() == "2"
    assert dialog._import_value.text() == "2"
    assert dialog._duplicate_value.text() == "0"
    assert dialog._warning_value.text() == "1"
    assert not dialog._technical.isVisibleTo(dialog)
    assert TARGET_FILENAME in report
    assert "计划新增：2" in report
    assert "西安" not in report
    assert "会保持原样复制" in dialog._warning_confirm.text()
    assert dialog._warning_confirm.isVisibleTo(dialog)
    assert dialog._execute_button.isEnabled()
    dialog._warning_confirm.setChecked(True)
    assert dialog._execute_button.isEnabled()
    dialog.close()


def test_import_dialog_is_preview_only_in_sandbox(qapp, tmp_path: Path):
    from src.ui.wanxiang_import_dialog import WanxiangImportDialog

    rime = _rime_dir(tmp_path)
    service = WanxiangImportService(
        rime, tmp_path / "backups", lambda: (True, "ok")
    )
    dialog = WanxiangImportDialog(service, sandbox_mode=True)

    dialog.scan()

    assert "沙盒模式" in dialog._notice.text()
    assert not dialog._execute_button.isEnabled()
    assert not (rime / TARGET_FILENAME).exists()
    dialog.close()


def test_import_dialog_keeps_technical_terms_out_of_primary_explanation(
        qapp, tmp_path: Path):
    from src.ui.wanxiang_import_dialog import WanxiangImportDialog

    rime = _rime_dir(tmp_path)
    service = WanxiangImportService(
        rime, tmp_path / "backups", lambda: (True, "ok")
    )
    dialog = WanxiangImportDialog(service)

    dialog.scan()
    primary_text = "\n".join((
        dialog.windowTitle(), dialog._notice.text(), dialog._status.text(),
        dialog._warning_confirm.text(),
    ))

    assert "SHA" not in primary_text
    assert "build/" not in primary_text
    assert "原子写入" not in primary_text
    assert TARGET_FILENAME not in primary_text
    assert TARGET_FILENAME in dialog._technical.toPlainText()
    dialog.close()


def test_import_button_explains_missing_warning_confirmation(
        qapp, tmp_path: Path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from src.ui.wanxiang_import_dialog import WanxiangImportDialog

    rime = _rime_dir(tmp_path)
    service = WanxiangImportService(
        rime, tmp_path / "backups", lambda: (True, "ok")
    )
    dialog = WanxiangImportDialog(service)
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )

    dialog.scan()
    dialog._execute()

    assert messages and messages[0][0] == "还差一步"
    assert "请先勾选" in dialog._status.text()
    assert not dialog._running
    assert not (rime / TARGET_FILENAME).exists()
    dialog.close()



def test_source_change_during_deploy_rolls_back_target(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    calls = 0

    def deploy():
        nonlocal calls
        calls += 1
        if calls == 1:
            (rime / "custom_phrase.txt").write_text("外部修改\twaibu\n", encoding="utf-8")
            build = rime / "build"
            build.mkdir(exist_ok=True)
            (build / "wanxiang.schema.yaml").write_text(
                f"custom_phrase:\n  user_dict: {TARGET_USER_DICT}\n",
                encoding="utf-8",
            )
        return True, "deployed"

    service = WanxiangImportService(rime, tmp_path / "backups", deploy)

    with pytest.raises(ImportExecutionError, match="导入期间发生变化"):
        service.execute(service.plan(), verify_timeout=1)

    assert not (rime / TARGET_FILENAME).exists()
    assert not (rime / "wanxiang.custom.yaml").exists()
    assert calls == 2


def test_rollback_never_overwrites_external_target_change(tmp_path: Path):
    rime = _rime_dir(tmp_path)
    service = None

    def deploy():
        assert service is not None
        service.target_path.write_text("外部目标\twaibu\n", encoding="utf-8")
        return False, "deploy failed"

    service = WanxiangImportService(rime, tmp_path / "backups", deploy)

    with pytest.raises(ImportExecutionError, match="未覆盖.*wanxiang_user_phrase"):
        service.execute(service.plan(), verify_timeout=0)

    assert service.target_path.read_text(encoding="utf-8") == "外部目标\twaibu\n"
    assert not service.patch_path.exists()
