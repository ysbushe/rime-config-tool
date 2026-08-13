from pathlib import Path

from src.main import _update_completion_request


def test_update_completion_request_accepts_managed_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    updates = tmp_path / "updates"
    updates.mkdir()
    marker = updates / "RimeConfig-directory-update.complete"
    monkeypatch.setattr("src.main.user_config_dir", lambda: tmp_path)

    result = _update_completion_request([
        "RimeConfig.exe", "--update-completed", "0.10.0", "v0.10.1",
        "--update-marker", marker.name,
    ])

    assert result == ("0.10.0", "v0.10.1", marker.resolve())


def test_update_completion_request_rejects_unmanaged_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("src.main.user_config_dir", lambda: tmp_path / "config")
    marker = tmp_path / "outside.complete"

    result = _update_completion_request([
        "RimeConfig.exe", "--update-completed", "0.10.0", "v0.10.1",
        "--update-marker", str(marker),
    ])

    assert result is None
