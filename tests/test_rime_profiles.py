from pathlib import Path

from src.service.rime_preview_service import RimePreviewService
from src.app_context import _active_schema_path
from src.service.rime_profiles import (
    active_schema_ids,
    detect_scheme_profiles,
    safe_preview_schema,
    scheme_display_state,
)


def _write_default(rime: Path, schema_ids: list[str], newline: str = "\n") -> None:
    rows = ["patch:", '  "schema_list":']
    rows.extend(f"    - schema: {schema_id}" for schema_id in schema_ids)
    (rime / "default.custom.yaml").write_text(
        newline.join(rows) + newline, encoding="utf-8"
    )


def test_detects_installed_and_active_frost_and_wanxiang(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.schema.yaml").write_text("schema: {}", encoding="utf-8")
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["wanxiang"])

    profiles = {profile.schema_id: profile for profile in detect_scheme_profiles(rime)}

    assert active_schema_ids(rime) == ("wanxiang",)
    assert profiles["rime_frost"].installed and not profiles["rime_frost"].active
    assert profiles["wanxiang"].installed and profiles["wanxiang"].active


def test_wanxiang_preview_uses_isolated_workspace(qapp, tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["wanxiang"], "\r\n")

    service = RimePreviewService(str(rime))
    assert service.schema_id == "wanxiang"
    assert "独立用户目录" in service.availability_message
    assert service._process is None


def test_frost_preview_remains_selected_when_enabled(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.schema.yaml").write_text("schema: {}", encoding="utf-8")
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["rime_frost", "wanxiang"])

    schema_id, message = safe_preview_schema(rime)
    service = RimePreviewService(str(rime))

    assert schema_id == "rime_frost"
    assert message == ""
    assert service.schema_id == "rime_frost"


def test_unknown_active_schema_is_read_only_for_preview(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    _write_default(rime, ["unknown_schema"])

    schema_id, message = safe_preview_schema(rime)

    assert schema_id is None
    assert "尚未" in message


def test_preview_reset_rechecks_active_scheme(tmp_path: Path):
    frost = tmp_path / "Frost"
    frost.mkdir()
    (frost / "rime_frost.schema.yaml").write_text("schema: {}", encoding="utf-8")
    wanxiang = tmp_path / "Wanxiang"
    wanxiang.mkdir()
    (wanxiang / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(wanxiang, ["wanxiang"])

    service = RimePreviewService(str(frost))
    assert service.schema_id == "rime_frost"

    service.reset(str(wanxiang))

    assert service.schema_id == "wanxiang"


def test_scheme_display_uses_enabled_single_scheme_not_stale_previous(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["wanxiang"])
    (rime / "user.yaml").write_text(
        "var:\n  previously_selected_schema: rime_frost\n", encoding="utf-8"
    )

    state = scheme_display_state(rime)

    assert state.summary == "当前方案：万象拼音"
    assert state.role == "success"
    assert state.active_schema_ids == ("wanxiang",)


def test_scheme_display_lists_multiple_and_labels_previous_as_last_used(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.schema.yaml").write_text("schema: {}", encoding="utf-8")
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["rime_frost", "wanxiang"])
    (rime / "user.yaml").write_text(
        "var:\n  previously_selected_schema: wanxiang\n", encoding="utf-8"
    )

    state = scheme_display_state(rime)

    assert state.summary == "已启用方案：白霜拼音、万象拼音"
    assert "上次使用：万象拼音" in state.detail
    assert state.role == "info"


def test_scheme_display_does_not_claim_unknown_scheme_is_compatible(tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    _write_default(rime, ["third_party"])

    state = scheme_display_state(rime)

    assert state.summary == "当前方案：未识别（third_party）"
    assert "尚未通过" in state.detail
    assert state.role == "warning"


def test_app_context_schema_path_follows_enabled_scheme_not_installed_frost(
        tmp_path: Path):
    rime = tmp_path / "Rime"
    rime.mkdir()
    (rime / "rime_frost.schema.yaml").write_text("schema: {}", encoding="utf-8")
    (rime / "wanxiang.schema.yaml").write_text("schema: {}", encoding="utf-8")
    _write_default(rime, ["wanxiang"])

    assert Path(_active_schema_path(str(rime))).name == "wanxiang.schema.yaml"

