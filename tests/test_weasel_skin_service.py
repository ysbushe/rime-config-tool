from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from src.service.backup_service import BackupService
from src.service.file_transaction import TransactionalFileWriter, capture_revision
from src.service.sandbox_service import SandboxService
from src.service.weasel_skin_service import (
    SkinSchemeDraft, WeaselSkinService, hex_to_rime_color, rime_color_to_hex,
)


def _service(root: Path) -> WeaselSkinService:
    return WeaselSkinService(
        root, BackupService(root, 3, root / "backups"),
        TransactionalFileWriter(root / "transactions"),
    )


def test_skin_patch_preserves_unrelated_text_comments_and_alias(tmp_path) -> None:
    target = tmp_path / "weasel.custom.yaml"
    target.write_text(
        "# owner's heading\ncustom_key: keep\npatch:\n"
        "  # keep this explanation\n"
        "  style/layout/border: 1 # former value\n"
        "  unrelated/value: yes\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)

    service.save_updates(
        target.name,
        {"style/layout/border_width": 3, "style/font_face": "Microsoft YaHei UI"},
        capture_revision(target),
    )

    text = target.read_text(encoding="utf-8")
    value = yaml.safe_load(text)
    assert "# owner's heading" in text
    assert "# keep this explanation" in text
    assert value["custom_key"] == "keep"
    assert value["patch"]["unrelated/value"] is True
    assert value["patch"]["style/layout/border"] == 3
    assert "style/layout/border_width" not in value["patch"]


def test_skin_scheme_save_and_delete_only_touch_custom_patch(tmp_path) -> None:
    (tmp_path / "weasel.yaml").write_text(
        "preset_color_schemes:\n  aqua:\n    name: Aqua\n    back_color: 0xffffff\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)
    revision = capture_revision(tmp_path / "weasel.custom.yaml")
    draft = SkinSchemeDraft(
        "my_skin", "我的皮肤", "Tester", "ABGR",
        {"back_color": "0xf0f0f0", "border_color": None},
    )

    service.save_scheme(draft, revision)
    catalog = {item.scheme_id: item for item in service.catalog()}
    assert catalog["aqua"].read_only
    assert not catalog["my_skin"].read_only
    assert service.load_scheme("my_skin").name == "我的皮肤"

    service.delete_custom_scheme(
        "my_skin", capture_revision(tmp_path / "weasel.custom.yaml"),
    )
    assert "my_skin" not in {item.scheme_id for item in service.catalog()}
    assert (tmp_path / "weasel.yaml").read_text(encoding="utf-8").startswith(
        "preset_color_schemes:"
    )


def test_skin_color_conversion_uses_familiar_rgba_display() -> None:
    assert rime_color_to_hex("0x332211", "ABGR") == "#112233"
    assert hex_to_rime_color("#112233", "ABGR") == "0x332211"
    assert rime_color_to_hex("0x80332211", "ABGR") == "#11223380"
    assert hex_to_rime_color("#11223380", "ABGR") == "0x80332211"
    assert hex_to_rime_color("#11223380", "ARGB") == "0x80112233"
    assert hex_to_rime_color("#11223380", "RGBA") == "0x11223380"


def test_inherit_writes_safe_deletion_over_older_nested_skin_block(tmp_path) -> None:
    (tmp_path / "weasel.yaml").write_text(
        "style:\n  font_face: Base Font\n  font_point: 14\n"
        "preset_color_schemes:\n  base:\n    name: Base\n",
        encoding="utf-8",
    )
    target = tmp_path / "weasel.custom.yaml"
    target.write_text(
        "patch:\n  style:\n    font_face: User Font\n"
        "  preset_color_schemes/+:\n    mine:\n      name: Mine\n"
        "      back_color: 0x332211\n",
        encoding="utf-8",
    )
    service = _service(tmp_path)
    effective, _custom, revision = service.read_effective()
    assert effective["style"]["font_face"] == "User Font"
    assert effective["style"]["font_point"] == 14

    service.save_updates(
        target.name, {}, revision,
        remove_keys=("preset_color_schemes/mine/back_color",),
    )
    value = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert value["patch"]["preset_color_schemes/mine/back_color"] is None
    assert "preset_color_schemes/+" in value["patch"]


def test_skin_sandbox_copies_effective_custom_preview_and_icons(tmp_path, monkeypatch) -> None:
    rime = tmp_path / "rime"
    (rime / "build").mkdir(parents=True)
    (rime / "preview").mkdir()
    (rime / "weasel.yaml").write_text("style: {}\n", encoding="utf-8")
    (rime / "weasel.custom.yaml").write_text("patch: {}\n", encoding="utf-8")
    (rime / "build" / "weasel.yaml").write_text("style: {}\n", encoding="utf-8")
    (rime / "preview" / "color_scheme_mine.png").write_bytes(b"png")
    (rime / "mine.ico").write_bytes(b"ico")
    state = tmp_path / "state"
    monkeypatch.setattr("src.service.sandbox_service.user_config_dir", lambda: state)
    service = SandboxService(SimpleNamespace(rime_dir=str(rime), sandbox_mode=True))

    sandbox = Path(service.active_rime_dir())

    assert (sandbox / "weasel.yaml").is_file()
    assert (sandbox / "weasel.custom.yaml").is_file()
    assert (sandbox / "build" / "weasel.yaml").is_file()
    assert (sandbox / "preview" / "color_scheme_mine.png").read_bytes() == b"png"
    assert (sandbox / "mine.ico").read_bytes() == b"ico"
