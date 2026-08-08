"""Capability-based Rime scheme discovery.

Detection is based on local configuration and the effective built default
schema list.  Unknown schemes remain visible to callers but are never assumed
to be compatible with White Frost-specific editors or preview behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SchemeProfile:
    schema_id: str
    display_name: str
    schema_filename: str
    installed: bool
    active: bool


@dataclass(frozen=True)
class SchemeDisplayState:
    """User-facing scheme status without claiming more than config proves."""

    summary: str
    detail: str
    role: str
    active_schema_ids: tuple[str, ...]
    last_selected_schema_id: str = ""


_KNOWN_SCHEMES = (
    (
        "rime_frost", "白霜拼音", "rime_frost.schema.yaml",
        (("rime_frost", 1.2), ("melt_eng", 1.1), ("radical_pinyin", 0.9)),
    ),
    (
        "wanxiang", "万象拼音", "wanxiang.schema.yaml",
        (
            ("wanxiang", 1.2), ("wanxiang_english", 1.1),
            ("wanxiang_mixedcode", 1.0), ("wanxiang_reverse", 0.9),
        ),
    ),
)
_SCHEME_NAMES = {
    schema_id: name for schema_id, name, _filename, _roots in _KNOWN_SCHEMES
}
_SCHEME_ROOTS = {
    schema_id: roots for schema_id, _name, _filename, roots in _KNOWN_SCHEMES
}


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _schema_list(data: dict) -> tuple[str, ...]:
    value = data.get("schema_list")
    patch = data.get("patch")
    if isinstance(patch, dict) and "schema_list" in patch:
        value = patch.get("schema_list")
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        schema_id = ""
        if isinstance(item, str):
            schema_id = item
        elif isinstance(item, dict):
            schema_id = str(item.get("schema", ""))
        schema_id = schema_id.strip()
        if schema_id and schema_id not in result:
            result.append(schema_id)
    return tuple(result)


def active_schema_ids(rime_dir: str | Path) -> tuple[str, ...]:
    root = Path(rime_dir)
    # The user patch is the clearest declaration.  The built default is the
    # effective fallback when patch syntax is inherited or generated.
    for path in (
        root / "default.custom.yaml",
        root / "build" / "default.yaml",
        root / "default.yaml",
    ):
        if not path.is_file():
            continue
        schema_ids = _schema_list(_read_yaml(path))
        if schema_ids:
            return schema_ids
    return ()


def last_selected_schema_id(rime_dir: str | Path) -> str:
    """Return Rime's persisted previous schema, which may lag live frontend state."""
    data = _read_yaml(Path(rime_dir) / "user.yaml")
    variables = data.get("var")
    if not isinstance(variables, dict):
        return ""
    return str(variables.get("previously_selected_schema", "")).strip()


def scheme_display_name(schema_id: str) -> str:
    return _SCHEME_NAMES.get(schema_id, f"未识别（{schema_id}）")


def scheme_title(schema_id: str) -> str:
    """Return a compact title for selectors that already show compatibility."""
    return _SCHEME_NAMES.get(schema_id, schema_id or "未识别方案")


def scheme_dictionary_roots(schema_id: str) -> tuple[tuple[str, float], ...]:
    """Return the ordered dictionary roots and translator quality for a scheme."""
    return _SCHEME_ROOTS.get(schema_id, ())


def scheme_display_state(rime_dir: str | Path) -> SchemeDisplayState:
    """Build a concise, honest status for the library page."""
    root = Path(rime_dir)
    active = active_schema_ids(root)
    previous = last_selected_schema_id(root)
    profiles = {profile.schema_id: profile for profile in detect_scheme_profiles(root)}

    if len(active) == 1:
        schema_id = active[0]
        name = scheme_display_name(schema_id)
        known = profiles.get(schema_id)
        if schema_id not in _SCHEME_NAMES:
            return SchemeDisplayState(
                f"当前方案：{name}",
                "该方案尚未通过本工具的兼容性验证。",
                "warning", active, previous,
            )
        if known is not None and not known.installed:
            return SchemeDisplayState(
                f"当前方案：{name}",
                "启用列表中存在该方案，但没有找到对应的方案文件。",
                "warning", active, previous,
            )
        return SchemeDisplayState(
            f"当前方案：{name}", "已从 Rime 启用列表确认。",
            "success", active, previous,
        )

    if len(active) > 1:
        names = "、".join(scheme_display_name(schema_id) for schema_id in active)
        detail = "Rime 同时启用了多个方案。"
        if previous in active:
            detail += f" 上次使用：{scheme_display_name(previous)}。"
        return SchemeDisplayState(
            f"已启用方案：{names}", detail, "info", active, previous,
        )

    installed = [profile.display_name for profile in profiles.values() if profile.installed]
    detail = (
        f"检测到已安装：{'、'.join(installed)}，但没有读到启用列表。"
        if installed else "没有找到可识别的拼音方案或启用列表。"
    )
    return SchemeDisplayState("当前方案：未识别", detail, "warning", (), previous)


def detect_scheme_profiles(rime_dir: str | Path) -> tuple[SchemeProfile, ...]:
    root = Path(rime_dir)
    active = set(active_schema_ids(root))
    return tuple(
        SchemeProfile(
            schema_id=schema_id,
            display_name=display_name,
            schema_filename=filename,
            installed=(root / filename).is_file(),
            active=schema_id in active,
        )
        for schema_id, display_name, filename, _roots in _KNOWN_SCHEMES
    )


def safe_preview_schema(rime_dir: str | Path) -> tuple[str | None, str]:
    """Return a schema that can run inside the disposable preview workspace."""
    root = Path(rime_dir)
    active = active_schema_ids(root)
    if "rime_frost" in active:
        return "rime_frost", ""
    if "wanxiang" in active:
        return "wanxiang", "万象候选将在独立用户目录中运行。"
    if active:
        return None, "当前启用方案尚未通过候选预览兼容性验证。"
    if (root / "rime_frost.schema.yaml").is_file():
        return "rime_frost", ""
    if (root / "wanxiang.schema.yaml").is_file():
        return "wanxiang", "万象候选将在独立用户目录中运行。"
    return None, "未检测到可安全预览的拼音方案。"

