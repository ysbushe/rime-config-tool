"""Scheme-scoped Rime files managed by the application.

This module is the single source of truth for write targets.  Callers must not
guess a personal dictionary from an installed schema or silently fall back to
White Frost when the live scheme is ambiguous.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.service.rime_profiles import (
    active_schema_ids,
    last_selected_schema_id,
    scheme_display_name,
)


@dataclass(frozen=True)
class ManagedSchemeFiles:
    schema_id: str
    display_name: str
    backup_prefix: str
    phrase_filename: str
    schema_filename: str
    symbols_filename: str = "symbols_v.yaml"
    display_filename: str = "pinyin_display.ini"

    @property
    def group_filename(self) -> str:
        return f"{self.phrase_filename}.groups.json"

    def backup_files(self) -> tuple[str, ...]:
        return (
            self.phrase_filename,
            self.group_filename,
            self.schema_filename,
            self.symbols_filename,
            self.display_filename,
        )


_SCHEMES: dict[str, ManagedSchemeFiles] = {
    "rime_frost": ManagedSchemeFiles(
        "rime_frost", "白霜拼音", "baishuang",
        "custom_phrase.txt", "rime_frost.schema.yaml",
    ),
    "wanxiang": ManagedSchemeFiles(
        "wanxiang", "万象拼音", "wanxiang",
        "wanxiang_user_phrase.txt", "wanxiang.schema.yaml",
    ),
}


@dataclass(frozen=True)
class ActiveSchemeResolution:
    schema_id: str
    files: ManagedSchemeFiles | None
    certain: bool
    reason: str

    @property
    def writable(self) -> bool:
        return bool(self.certain and self.files is not None)


def managed_scheme(schema_id: str) -> ManagedSchemeFiles | None:
    return _SCHEMES.get(str(schema_id or "").strip())


def known_managed_schemes() -> tuple[ManagedSchemeFiles, ...]:
    return tuple(_SCHEMES.values())


def resolve_active_scheme(rime_dir: str | Path) -> ActiveSchemeResolution:
    """Resolve the best observable live scheme without unsafe fallback.

    ``user.yaml`` is Rime's persisted frontend selection and can lag a switch
    briefly.  A single enabled schema is authoritative; with multiple enabled
    schemas the persisted selection is accepted only when it belongs to the
    enabled list.  Ambiguous/unknown states are deliberately read-only.
    """
    root = Path(rime_dir) if rime_dir else Path()
    if not rime_dir or not root.is_dir():
        return ActiveSchemeResolution("", None, False, "Rime 目录无效")
    enabled = active_schema_ids(root)
    previous = last_selected_schema_id(root)
    if previous and previous in enabled:
        files = managed_scheme(previous)
        if files is None:
            return ActiveSchemeResolution(
                previous, None, False,
                f"当前方案 {scheme_display_name(previous)} 尚未适配",
            )
        return ActiveSchemeResolution(previous, files, True, "已从 Rime 当前选择确认")
    if len(enabled) == 1:
        schema_id = enabled[0]
        files = managed_scheme(schema_id)
        if files is None:
            return ActiveSchemeResolution(
                schema_id, None, False,
                f"唯一启用方案 {scheme_display_name(schema_id)} 尚未适配",
            )
        return ActiveSchemeResolution(schema_id, files, True, "已从唯一启用方案确认")
    if len(enabled) > 1:
        return ActiveSchemeResolution(
            previous, managed_scheme(previous), False,
            "Rime 启用了多个方案，但尚未确认当前正在使用的方案",
        )
    return ActiveSchemeResolution("", None, False, "未读取到 Rime 启用方案")
