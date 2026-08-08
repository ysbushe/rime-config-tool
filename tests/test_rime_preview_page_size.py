from src.service.rime_preview_service import _configured_page_size


def test_configured_page_size_prefers_deployed_default(tmp_path) -> None:
    (tmp_path / "build").mkdir()
    (tmp_path / "default.yaml").write_text(
        "menu:\n  page_size: 4\n", encoding="utf-8"
    )
    (tmp_path / "build" / "default.yaml").write_text(
        "menu:\n  page_size: 6\n", encoding="utf-8"
    )

    assert _configured_page_size(str(tmp_path)) == 6


def test_configured_page_size_is_safely_bounded(tmp_path) -> None:
    (tmp_path / "default.yaml").write_text(
        "menu:\n  page_size: 99\n", encoding="utf-8"
    )

    assert _configured_page_size(str(tmp_path)) == 10
