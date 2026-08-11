from __future__ import annotations

import json
from pathlib import Path
import shutil
import zipfile

import pytest

from src.service.backup_service import BackupService
from src.service.file_transaction import FileTransactionConflict, TransactionalFileWriter
from src.service.group_service import GroupService
from src.service.managed_files import resolve_active_scheme
from src.service.rime_patch_service import RimePatchService
from src.service.wanxiang_update_service import (
    WanxiangUpdateReport,
    WanxiangUpdateService,
)


def _write_scheme_state(root: Path, selected: str, enabled: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "user.yaml").write_text(
        f"var:\n  previously_selected_schema: {selected}\n", encoding="utf-8",
    )
    schemas = "\n".join(f"    - schema: {item}" for item in enabled)
    (root / "default.custom.yaml").write_text(
        "patch:\n  schema_list:\n" + schemas + "\n", encoding="utf-8",
    )


def test_scheme_resolution_uses_live_selection_without_white_frost_fallback(tmp_path) -> None:
    _write_scheme_state(tmp_path, "wanxiang", ["rime_frost", "wanxiang"])

    result = resolve_active_scheme(tmp_path)

    assert result.writable
    assert result.schema_id == "wanxiang"
    assert result.files is not None
    assert result.files.phrase_filename == "wanxiang_user_phrase.txt"


def test_scheme_resolution_is_read_only_when_multiple_schemes_are_ambiguous(tmp_path) -> None:
    _write_scheme_state(tmp_path, "unknown", ["rime_frost", "wanxiang"])

    result = resolve_active_scheme(tmp_path)

    assert not result.writable
    assert "多个方案" in result.reason


def test_scheme_backup_namespaces_do_not_mix(tmp_path) -> None:
    (tmp_path / "custom_phrase.txt").write_text("白霜\n", encoding="utf-8")
    (tmp_path / "wanxiang_user_phrase.txt").write_text("万象\n", encoding="utf-8")
    backup_root = tmp_path / "backups"
    frost = BackupService(tmp_path, 3, backup_root, namespace="baishuang")
    wanxiang = BackupService(tmp_path, 3, backup_root, namespace="wanxiang")

    assert frost.backup("custom_phrase.txt") is not None
    assert wanxiang.backup("wanxiang_user_phrase.txt") is not None
    assert frost.list_backups("wanxiang_user_phrase.txt") == []
    assert wanxiang.list_backups("custom_phrase.txt") == []


def test_missing_scheme_groups_restore_only_matching_entries(tmp_path) -> None:
    source = tmp_path / "custom_phrase.txt.groups.json"
    source.write_text(json.dumps({
        "groups": ["工作", "人名"],
        "membership": {
            "共同\tgongtong": "工作",
            "只在来源\tzhizailaiyuan": "人名",
        },
    }, ensure_ascii=False), encoding="utf-8")
    groups = GroupService(str(tmp_path), "wanxiang_user_phrase.txt")

    restored = groups.restore_compatible_sidecar(
        source, {"共同\tgongtong"},
        TransactionalFileWriter(tmp_path / "journals"),
    )

    assert restored == 1
    assert groups.list_groups() == ["工作", "人名"]
    assert groups.get_entry_group("共同", "gongtong") == "工作"
    assert groups.get_entry_group("只在来源", "zhizailaiyuan") == ""


def test_rime_patch_save_preserves_unrelated_values_and_rejects_conflict(tmp_path) -> None:
    target = tmp_path / "default.custom.yaml"
    target.write_text(
        "custom_key: keep\npatch:\n  menu/page_size: 5\n", encoding="utf-8",
    )
    service = RimePatchService(
        str(tmp_path), BackupService(tmp_path, 3),
        TransactionalFileWriter(tmp_path / "journals"),
    )
    _data, revision = service.read(target.name)
    service.save_updates(target.name, {"menu/page_size": 7}, revision)
    value, stale_revision = service.read(target.name)
    assert value["custom_key"] == "keep"
    assert value["patch"]["menu/page_size"] == 7

    target.write_text(target.read_text(encoding="utf-8") + "external: true\n", encoding="utf-8")
    with pytest.raises(FileTransactionConflict):
        service.save_updates(target.name, {"menu/page_size": 8}, stale_revision)


def test_wanxiang_version_prefers_numeric_version_file(
        tmp_path, monkeypatch) -> None:
    (tmp_path / "version.txt").write_text("17.2.2\n", encoding="utf-8")
    (tmp_path / "wanxiang.schema.yaml").write_text(
        'schema:\n  version: "LTS"\n', encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.service.wanxiang_update_service.user_config_dir", lambda: tmp_path / "state",
    )
    service = WanxiangUpdateService(
        str(tmp_path), BackupService(tmp_path, 3),
        TransactionalFileWriter(tmp_path / "journals"),
    )

    assert service.current_version() == "17.2.2"


def _wanxiang_schema(version: str) -> bytes:
    return (
        "schema:\n"
        "  schema_id: wanxiang\n"
        f"  version: {version}\n"
        "translator:\n"
        "  enable_user_dict: true\n"
        "  contextual_suggestions: false\n"
        "custom_phrase:\n"
        "  user_dict: custom_phrase\n"
        "wanxiang_english:\n"
        "  english_spacing: smart\n"
        "  max_candidates: 5\n"
        "wanxiang_lookup:\n"
        "  enable_tone: true\n"
        "key_binder:\n"
        "  bindings: []\n"
    ).encode("utf-8")


def _make_wanxiang_zip(path: Path, schema_payload: bytes, dict_payload: bytes,
                       version: str = "2.0") -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("rime-wanxiang/wanxiang.schema.yaml", schema_payload)
        package.writestr("rime-wanxiang/wanxiang.dict.yaml", dict_payload)
        package.writestr("rime-wanxiang/version.txt", version.encode("utf-8"))
        package.writestr("rime-wanxiang/wanxiang_algebra.yaml", b"base: {}\n")
        package.writestr("rime-wanxiang/lua/wanxiang/super_english.lua", b"return {}\n")
        package.writestr("rime-wanxiang/lua/wanxiang/super_lookup.lua", b"return {}\n")
        package.writestr("rime-wanxiang/wanxiang.custom.yaml", b"protected")
        package.writestr("rime-wanxiang/wanxiang_user_phrase.txt", b"protected")


def test_wanxiang_update_snapshot_restores_every_managed_file(
        tmp_path, monkeypatch) -> None:
    rime = tmp_path / "rime"
    rime.mkdir()
    schema = rime / "wanxiang.schema.yaml"
    dictionary = rime / "wanxiang.dict.yaml"
    personal = rime / "wanxiang_user_phrase.txt"
    schema.write_bytes(b"schema:\n  version: 1.0\n")
    dictionary.write_bytes(b"old dictionary")
    personal.write_bytes(b"personal")
    state = tmp_path / "state"
    monkeypatch.setattr(
        "src.service.wanxiang_update_service.user_config_dir", lambda: state,
    )
    service = WanxiangUpdateService(
        str(rime), BackupService(rime, 3, tmp_path / "backups", namespace="wanxiang"),
        TransactionalFileWriter(tmp_path / "journals"),
    )
    archive = tmp_path / "release.zip"
    _make_wanxiang_zip(
        archive, _wanxiang_schema("2.0"), b"new dictionary",
    )
    files, layout = service._inspect(archive)
    service._save_manifest("1.0", files, layout, archive, adopt=True)
    monkeypatch.setattr(
        service, "_download_archive", lambda _url, destination: shutil.copy2(archive, destination),
    )
    report = WanxiangUpdateReport(
        "1.0", "2.0", "test", True, "ok",
        "https://github.com/amzxyz/rime-wanxiang/releases/download/v2.0/base.zip",
        preflight_passed=True,
    )

    ok, _message = service.update(report)
    assert ok
    assert dictionary.read_bytes() == b"new dictionary"
    assert personal.read_bytes() == b"personal"

    rollback_ok, _rollback_message = service.rollback_last_update()
    assert rollback_ok
    assert schema.read_bytes() == b"schema:\n  version: 1.0\n"
    assert dictionary.read_bytes() == b"old dictionary"
    assert personal.read_bytes() == b"personal"


def test_wanxiang_capability_preflight_accepts_newer_unregistered_package(
        tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.service.wanxiang_update_service.user_config_dir", lambda: tmp_path / "state",
    )
    service = WanxiangUpdateService(
        str(tmp_path), BackupService(tmp_path, 3),
        TransactionalFileWriter(tmp_path / "journals"),
    )
    archive = tmp_path / "future.zip"
    _make_wanxiang_zip(archive, _wanxiang_schema("99.1.0"), b"dictionary", "99.1.0")

    files, layout = service._inspect(archive)
    service._validate_capabilities("99.1.0", files, layout)

    assert "wanxiang.custom.yaml" not in files
    assert "wanxiang_user_phrase.txt" not in files


def test_wanxiang_check_uses_capability_preflight_instead_of_version_block(
        tmp_path, monkeypatch) -> None:
    rime = tmp_path / "rime"
    rime.mkdir()
    (rime / "version.txt").write_text("17.2.2", encoding="utf-8")
    monkeypatch.setattr(
        "src.service.wanxiang_update_service.user_config_dir", lambda: tmp_path / "state",
    )
    service = WanxiangUpdateService(
        str(rime), BackupService(rime, 3),
        TransactionalFileWriter(tmp_path / "journals"),
    )
    archive = tmp_path / "future.zip"
    _make_wanxiang_zip(archive, _wanxiang_schema("99.1.0"), b"dictionary", "99.1.0")
    release = {
        "tag_name": "v99.1.0",
        "assets": [{
            "name": "rime-wanxiang-base.zip",
            "browser_download_url": (
                "https://github.com/amzxyz/rime-wanxiang/"
                "releases/download/v99.1.0/rime-wanxiang-base.zip"
            ),
        }],
    }
    monkeypatch.setattr(service, "_read_json_url", lambda *_args, **_kwargs: release)
    monkeypatch.setattr(service, "_obtain_archive", lambda *_args: archive)
    monkeypatch.setattr(
        service, "_load_compatibility_rules",
        lambda: ({"verified_through": "17.2.4", "blocked_versions": {}}, "test rules"),
    )

    report = service.check()

    assert report.compatible
    assert report.preflight_passed
    assert report.requires_confirmation
    assert not report.waiting_for_adapter


def test_wanxiang_force_update_preserves_custom_files_and_rotates_recovery(
        tmp_path, monkeypatch) -> None:
    rime = tmp_path / "rime"
    rime.mkdir()
    (rime / "wanxiang.schema.yaml").write_bytes(_wanxiang_schema("1.0"))
    (rime / "wanxiang.dict.yaml").write_bytes(b"locally changed")
    custom = rime / "wanxiang.custom.yaml"
    personal = rime / "wanxiang_user_phrase.txt"
    custom.write_bytes(b"custom settings")
    personal.write_bytes(b"personal phrases")
    state = tmp_path / "state"
    monkeypatch.setattr(
        "src.service.wanxiang_update_service.user_config_dir", lambda: state,
    )
    service = WanxiangUpdateService(
        str(rime), BackupService(rime, 3, tmp_path / "backups", namespace="wanxiang"),
        TransactionalFileWriter(tmp_path / "journals"),
    )
    archive = tmp_path / "release.zip"
    _make_wanxiang_zip(archive, _wanxiang_schema("2.0"), b"new dictionary")
    files, layout = service._inspect(archive)
    service._save_manifest("1.0", files, layout, archive, adopt=False)
    monkeypatch.setattr(
        service, "_download_archive", lambda _url, destination: shutil.copy2(archive, destination),
    )
    report = WanxiangUpdateReport(
        "1.0", "2.0", "test", True, "preflight ok",
        "https://github.com/amzxyz/rime-wanxiang/releases/download/v2.0/base.zip",
        preflight_passed=True,
    )

    normal_ok, normal_message = service.update(report)
    assert not normal_ok
    assert normal_message.startswith("需要强制更新：")

    forced_ok, _forced_message = service.update(report, force=True)
    assert forced_ok
    assert (rime / "wanxiang.dict.yaml").read_bytes() == b"new dictionary"
    assert custom.read_bytes() == b"custom settings"
    assert personal.read_bytes() == b"personal phrases"
    assert service.has_recovery_backup()

    confirmed, _confirm_message = service.confirm_update()
    assert confirmed
    assert (
        state / "wanxiang-update" / "recovery" / "current"
        / "files" / "wanxiang.custom.yaml"
    ).read_bytes() == b"custom settings"
