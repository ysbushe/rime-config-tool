from pathlib import Path

from src.service.rime_preview_workspace import PreviewWorkspace


def _source(root: Path) -> Path:
    source = root / "Rime"
    (source / "lua").mkdir(parents=True)
    (source / "build").mkdir()
    (source / "wanxiang.userdb").mkdir()
    (source / "default.custom.yaml").write_text("patch: {}\n", encoding="utf-8")
    (source / "wanxiang-lts-zh-hans.gram").write_bytes(b"grammar")
    (source / "lua" / "helper.lua").write_text("return {}\n", encoding="utf-8")
    (source / "build" / "wanxiang.table.bin").write_bytes(b"prebuilt")
    (source / "wanxiang.userdb" / "000001.ldb").write_bytes(b"learned")
    return source


def test_preview_workspace_isolates_writable_files_and_reuses_snapshot(tmp_path):
    source = _source(tmp_path)
    workspace = PreviewWorkspace(tmp_path / "preview")

    first = workspace.prepare(source, "wanxiang")
    second = workspace.prepare(source, "wanxiang")

    assert first.path == second.path
    assert (first.path / "default.custom.yaml").read_text(encoding="utf-8") == "patch: {}\n"
    assert (first.path / "lua" / "helper.lua").is_file()
    assert (first.path / "wanxiang.userdb" / "000001.ldb").read_bytes() == b"learned"
    assert first.copied_userdbs == ("wanxiang.userdb",)
    assert not (first.path / "build" / "wanxiang.table.bin").exists()


def test_preview_workspace_reports_userdb_that_cannot_be_copied(
        tmp_path, monkeypatch):
    source = _source(tmp_path)
    workspace = PreviewWorkspace(tmp_path / "preview")
    real_copy = __import__("shutil").copy2

    def fail_userdb(path, target, *args, **kwargs):
        if ".userdb" in str(path):
            raise PermissionError("locked")
        return real_copy(path, target, *args, **kwargs)

    monkeypatch.setattr("src.service.rime_preview_workspace.shutil.copy2", fail_userdb)

    report = workspace.prepare(source, "wanxiang")

    assert report.skipped_userdbs == ("wanxiang.userdb",)
    assert not (report.path / "wanxiang.userdb").exists()
