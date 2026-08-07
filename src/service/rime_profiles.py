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
    ("rime_frost", "白霜拼音", "rime_frost.schema.yaml"),
    ("wanxiang", "万象拼音", "wanxiang.schema.yaml"),
)
_SCHEME_NAMES = {schema_id: name for schema_id, name, _filename in _KNOWN_SCHEMES}


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
        for schema_id, display_name, filename in _KNOWN_SCHEMES
    )


def safe_preview_schema(rime_dir: str | Path) -> tuple[str | None, str]:
    """Return a safe schema id or an explicit reason for disabling preview."""
    root = Path(rime_dir)
    active = active_schema_ids(root)
    if "rime_frost" in active:
        return "rime_frost", ""
    if "wanxiang" in active:
        return None, (
            "当前启用万象拼音；隔离预览环境尚未就绪，"
            "为避免访问真实万象用户库，候选预览已停用。"
        )
    if active:
        return None, "当前启用方案尚未通过候选预览兼容性验证。"
    if (root / "rime_frost.schema.yaml").is_file():
        return "rime_frost", ""
    if (root / "wanxiang.schema.yaml").is_file():
        return None, (
            "仅检测到万象拼音；隔离预览环境尚未就绪，候选预览已停用。"
        )
    return None, "未检测到可安全预览的拼音方案。"

