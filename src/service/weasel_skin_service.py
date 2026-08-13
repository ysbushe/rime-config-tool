"""Weasel skin drafts and comment-preserving custom patch writes.

The editor reads effective values from ``build/weasel.yaml`` when available,
but it only writes explicit slash-path patches to ``weasel.custom.yaml`` (or a
schema custom file).  Existing unknown keys, ordering and comments are kept.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable
import zipfile

import yaml

from src.service.file_transaction import (
    FileChange,
    FileRevision,
    FileTransactionConflict,
    TransactionalFileWriter,
    capture_revision,
)
from src.service.backup_service import BackupService


COLOR_FIELDS: tuple[str, ...] = (
    "back_color", "border_color", "shadow_color",
    "text_color", "label_color", "candidate_text_color", "comment_text_color",
    "hilited_text_color", "hilited_back_color", "hilited_shadow_color",
    "candidate_back_color", "candidate_border_color", "candidate_shadow_color",
    "hilited_candidate_text_color", "hilited_candidate_back_color",
    "hilited_label_color", "hilited_comment_text_color",
    "hilited_candidate_border_color", "hilited_candidate_shadow_color",
    "hilited_mark_color", "nextpage_color", "prevpage_color",
)


def rime_color_to_hex(value: Any, color_format: str = "ABGR") -> str:
    """Convert Weasel's channel order to the familiar #RRGGBB[AA] form."""
    raw = str(value or "").strip().lower().removeprefix("0x").removeprefix("#")
    if len(raw) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", raw):
        return "#000000"
    fmt = str(color_format or "ABGR").upper()
    if len(raw) == 6:
        return f"#{raw[4:6]}{raw[2:4]}{raw[0:2]}" if fmt == "ABGR" else f"#{raw}"
    pairs = [raw[index:index + 2] for index in range(0, 8, 2)]
    if fmt == "ABGR":
        aa, bb, gg, rr = pairs
    elif fmt == "ARGB":
        aa, rr, gg, bb = pairs
    else:  # RGBA
        rr, gg, bb, aa = pairs
    return f"#{rr}{gg}{bb}{aa}"


def hex_to_rime_color(value: str, color_format: str = "ABGR") -> str:
    """Convert #RRGGBB[AA] back to the selected Weasel channel order."""
    raw = str(value or "").strip().lower().removeprefix("#")
    if len(raw) not in (6, 8) or not re.fullmatch(r"[0-9a-f]+", raw):
        raise ValueError("颜色应为 #RRGGBB 或 #RRGGBBAA")
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    aa = raw[6:8] if len(raw) == 8 else "ff"
    fmt = str(color_format or "ABGR").upper()
    if fmt == "ABGR":
        return "0x" + (bb + gg + rr if len(raw) == 6 else aa + bb + gg + rr)
    if fmt == "ARGB":
        return "0x" + (rr + gg + bb if len(raw) == 6 else aa + rr + gg + bb)
    return "0x" + (rr + gg + bb if len(raw) == 6 else rr + gg + bb + aa)


@dataclass
class SkinSchemeDraft:
    scheme_id: str
    name: str = ""
    author: str = ""
    color_format: str = "ABGR"
    colors: dict[str, str | None] = field(default_factory=dict)
    origin: str = "用户自定义"
    read_only: bool = False


@dataclass
class WeaselStyleDraft:
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class SchemaAppearanceDraft:
    schema_id: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkinCatalogItem:
    scheme_id: str
    name: str
    origin: str
    read_only: bool


def _safe_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"无法安全解析 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{path.name} 顶层结构不是 YAML 映射")
    return value


def _lookup(mapping: dict[str, Any], path: str, default: Any = None) -> Any:
    if path in mapping:
        return mapping[path]
    current: Any = mapping
    for part in path.split("/"):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _merge_effective(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge enough Rime patch semantics for editor reads."""
    result = yaml.safe_load(yaml.safe_dump(base, allow_unicode=True)) or {}
    for raw_key, value in patch.items():
        key = str(raw_key)
        if key.endswith("/+"):
            target_path = key[:-2]
            target = _ensure_path(result, target_path)
            if isinstance(target, dict) and isinstance(value, dict):
                target.update(value)
            continue
        parts = key.split("/")
        current = result
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        if value is None:
            current.pop(parts[-1], None)
        elif isinstance(value, dict) and isinstance(current.get(parts[-1]), dict):
            _deep_update(current[parts[-1]], value)
        else:
            current[parts[-1]] = value
    return result


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _ensure_path(mapping: dict[str, Any], path: str) -> dict[str, Any]:
    current = mapping
    for part in path.split("/"):
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    return current


class WeaselSkinService:
    """Data boundary for the full Weasel skin editor."""

    def __init__(self, root: str | Path, backup: BackupService,
                 writer: TransactionalFileWriter) -> None:
        self.root = Path(root)
        self.backup = backup
        self.writer = writer

    @classmethod
    def from_patch_service(cls, service) -> "WeaselSkinService":
        return cls(service.root, service.backup, service.writer)

    @property
    def custom_path(self) -> Path:
        return self.root / "weasel.custom.yaml"

    def read_effective(self) -> tuple[dict[str, Any], dict[str, Any], FileRevision]:
        build = self.root / "build" / "weasel.yaml"
        source = build if build.is_file() else self.root / "weasel.yaml"
        base = _safe_mapping(source)
        custom = _safe_mapping(self.custom_path)
        patch = custom.get("patch") or {}
        if not isinstance(patch, dict):
            raise RuntimeError("weasel.custom.yaml 的 patch 节点不是映射")
        return _merge_effective(base, patch), custom, capture_revision(self.custom_path)

    def catalog(self) -> list[SkinCatalogItem]:
        effective, custom, _revision = self.read_effective()
        schemes = effective.get("preset_color_schemes") or {}
        if not isinstance(schemes, dict):
            schemes = {}
        custom_ids = self._custom_scheme_ids(custom.get("patch") or {})
        wanxiang_source = _safe_mapping(self.root / "weasel.yaml")
        wanxiang_ids = set((wanxiang_source.get("preset_color_schemes") or {}).keys())
        items: list[SkinCatalogItem] = []
        for scheme_id, raw in schemes.items():
            if not isinstance(raw, dict):
                continue
            sid = str(scheme_id)
            if sid in custom_ids:
                origin, read_only = "用户自定义", False
            elif sid in wanxiang_ids and (self.root / "wanxiang.schema.yaml").is_file():
                origin, read_only = "万象预设", True
            else:
                origin, read_only = "小狼毫内置", True
            items.append(SkinCatalogItem(
                sid, str(raw.get("name") or sid), origin, read_only,
            ))
        return sorted(items, key=lambda item: (item.origin, item.name.casefold()))

    def load_scheme(self, scheme_id: str) -> SkinSchemeDraft:
        effective, custom, _revision = self.read_effective()
        raw = _lookup(effective, f"preset_color_schemes/{scheme_id}", {})
        if not isinstance(raw, dict):
            raw = {}
        item = next((x for x in self.catalog() if x.scheme_id == scheme_id), None)
        return SkinSchemeDraft(
            scheme_id=scheme_id,
            name=str(raw.get("name") or scheme_id),
            author=str(raw.get("author") or ""),
            color_format=str(raw.get("color_format") or "ABGR").upper(),
            colors={name: raw.get(name) for name in COLOR_FIELDS},
            origin=item.origin if item else "用户自定义",
            read_only=item.read_only if item else False,
        )

    def load_style(self) -> WeaselStyleDraft:
        effective, _custom, _revision = self.read_effective()
        style = effective.get("style") or {}
        if not isinstance(style, dict):
            style = {}
        layout = style.get("layout") or {}
        if not isinstance(layout, dict):
            layout = {}
        values = dict(style)
        values["layout"] = dict(layout)
        return WeaselStyleDraft(values)

    def save_style(self, draft: WeaselStyleDraft, expected: FileRevision) -> Path:
        updates: dict[str, Any] = {}
        for key, value in draft.values.items():
            if key == "layout" and isinstance(value, dict):
                for child, child_value in value.items():
                    updates[f"style/layout/{child}"] = child_value
            else:
                updates[f"style/{key}"] = value
        return self.save_updates("weasel.custom.yaml", updates, expected)

    def save_scheme(self, draft: SkinSchemeDraft, expected: FileRevision) -> Path:
        prefix = f"preset_color_schemes/{draft.scheme_id}"
        updates: dict[str, Any] = {
            f"{prefix}/name": draft.name or draft.scheme_id,
            f"{prefix}/author": draft.author,
            f"{prefix}/color_format": draft.color_format,
        }
        remove_keys: list[str] = []
        for key, value in draft.colors.items():
            if key in COLOR_FIELDS and value not in (None, ""):
                updates[f"{prefix}/{key}"] = value
            elif key in COLOR_FIELDS:
                remove_keys.append(f"{prefix}/{key}")
        return self.save_updates(
            "weasel.custom.yaml", updates, expected,
            remove_keys=tuple(remove_keys),
        )

    def delete_custom_scheme(self, scheme_id: str, expected: FileRevision) -> Path:
        item = next((x for x in self.catalog() if x.scheme_id == scheme_id), None)
        if item is None or item.read_only:
            raise RuntimeError("内置或万象预设皮肤不能直接删除，请复制后编辑。")
        return self.save_updates(
            "weasel.custom.yaml", {}, expected,
            remove_prefixes=(f"preset_color_schemes/{scheme_id}/",),
        )

    def save_schema_appearance(self, draft: SchemaAppearanceDraft,
                               expected: FileRevision) -> Path:
        filename = f"{draft.schema_id}.custom.yaml"
        updates = {str(key): value for key, value in draft.values.items()}
        return self.save_updates(filename, updates, expected)

    def read_schema_appearance(self, schema_id: str) -> tuple[SchemaAppearanceDraft, FileRevision]:
        filename = f"{schema_id}.custom.yaml"
        path = self.root / filename
        data = _safe_mapping(path)
        patch = data.get("patch") or {}
        if not isinstance(patch, dict):
            raise RuntimeError(f"{filename} 的 patch 节点不是映射")
        return SchemaAppearanceDraft(schema_id, dict(patch)), capture_revision(path)

    def scheme_yaml(self, draft: SkinSchemeDraft) -> str:
        body: dict[str, Any] = {
            "name": draft.name or draft.scheme_id,
            "author": draft.author,
            "color_format": draft.color_format,
        }
        body.update({key: value for key, value in draft.colors.items() if value not in (None, "")})
        return yaml.safe_dump(
            {draft.scheme_id: body}, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=4096,
        )

    def import_scheme(self, payload: str) -> SkinSchemeDraft:
        try:
            value = yaml.safe_load(payload) or {}
        except yaml.YAMLError as exc:
            raise RuntimeError(f"皮肤 YAML 无法解析：{exc}") from exc
        if not isinstance(value, dict) or len(value) != 1:
            raise RuntimeError("皮肤文件应只包含一个皮肤 ID。")
        scheme_id, raw = next(iter(value.items()))
        if not isinstance(raw, dict):
            raise RuntimeError("皮肤内容不是有效的配置映射。")
        sid = re.sub(r"[^a-zA-Z0-9_-]", "_", str(scheme_id)).strip("_")
        if not sid:
            raise RuntimeError("皮肤 ID 无效。")
        return SkinSchemeDraft(
            sid, str(raw.get("name") or sid), str(raw.get("author") or ""),
            str(raw.get("color_format") or "ABGR").upper(),
            {key: raw.get(key) for key in COLOR_FIELDS}, "用户自定义", False,
        )

    def export_package(self, draft: SkinSchemeDraft, target: Path,
                       extras: Iterable[Path] = ()) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(f"{draft.scheme_id}.yaml", self.scheme_yaml(draft))
            for path in extras:
                if path.is_file():
                    package.write(path, f"assets/{path.name}")
        return target

    def save_updates(self, filename: str, updates: dict[str, Any],
                     expected: FileRevision, *,
                     remove_prefixes: tuple[str, ...] = (),
                     remove_keys: tuple[str, ...] = ()) -> Path:
        path = self._target(filename)
        if capture_revision(path) != expected:
            raise FileTransactionConflict(f"文件已被其他程序修改，本次未写入：{path}")
        original = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        if original:
            _safe_mapping(path)  # Parse failure must block every write.
        current = _safe_mapping(path) if path.is_file() else {"patch": {}}
        current_patch = current.get("patch") or {}
        for key in remove_keys:
            if _nested_patch_contains(current_patch, key):
                # Rime treats a null slash-path patch as deletion.  Keeping an
                # explicit deletion is necessary when an older nested block
                # would otherwise become visible again.
                updates.setdefault(key, None)
        updates = _apply_alias_preferences(dict(updates), current_patch)
        payload = _patch_text(
            original, updates, remove_prefixes, remove_keys,
        ).encode("utf-8")
        # Verify the result too, before a backup or transaction is attempted.
        try:
            parsed = yaml.safe_load(payload.decode("utf-8")) or {}
        except yaml.YAMLError as exc:
            raise RuntimeError(f"生成的 YAML 无法安全解析：{exc}") from exc
        if (
            not isinstance(parsed, dict)
            or parsed.get("patch") is not None
            and not isinstance(parsed.get("patch"), dict)
        ):
            raise RuntimeError("生成的配置缺少有效 patch 节点，已停止保存。")
        if expected.exists and self.backup.backup(filename) is None:
            raise RuntimeError(f"{filename} 备份失败，已停止保存")
        self.writer.commit([FileChange(path, payload, expected)])
        return path

    def _target(self, filename: str) -> Path:
        if not self.root.is_dir():
            raise RuntimeError("Rime 目录无效")
        path = (self.root / filename).resolve()
        if path.parent != self.root.resolve():
            raise RuntimeError("拒绝访问 Rime 目录之外的配置文件")
        return path

    @staticmethod
    def _custom_scheme_ids(patch: dict[str, Any]) -> set[str]:
        result: set[str] = set()
        for container in ("preset_color_schemes/+", "preset_color_schemes"):
            nested = patch.get(container)
            if isinstance(nested, dict):
                result.update(str(key) for key in nested)
        for key in patch:
            match = re.match(r"preset_color_schemes/([^/]+)(?:/|$)", str(key))
            if match:
                result.add(match.group(1))
        return result


_PATCH_RE = re.compile(r"^(?P<indent>[ \t]*)patch\s*:\s*(?:#.*)?$")


def _apply_alias_preferences(updates: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Keep aliases already used by the user's custom file."""
    aliases = {
        "style/layout/border_width": "style/layout/border",
        "style/layout/hilited_corner_radius": "style/layout/round_corner",
        "style/layout/hilite_padding_x": "style/layout/hilite_padding",
    }
    for canonical, alias in aliases.items():
        if canonical in updates and alias in patch and canonical not in patch:
            updates[alias] = updates.pop(canonical)
    type_value = updates.get("style/layout/type")
    old_boolean_keys = {
        "style/horizontal", "style/fullscreen", "style/vertical_text",
    }
    if type_value is not None and old_boolean_keys.intersection(patch):
        mode = str(updates.pop("style/layout/type"))
        updates["style/horizontal"] = mode.startswith("horizontal")
        updates["style/fullscreen"] = mode.endswith("+fullscreen")
        updates["style/vertical_text"] = mode == "vertical_text"
    return updates


def _nested_patch_contains(patch: dict[str, Any], path: str) -> bool:
    parts = path.split("/")
    current: Any = patch
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]
    if current is not None:
        return True
    if parts[:1] == ["preset_color_schemes"]:
        current = patch.get("preset_color_schemes/+")
        for part in parts[1:]:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True
    return False


def _patch_text(original: str, updates: dict[str, Any],
                remove_prefixes: tuple[str, ...],
                remove_keys: tuple[str, ...] = ()) -> str:
    """Update direct patch entries while retaining unrelated source text."""
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.replace("\r\n", "\n").splitlines()
    patch_index = next((i for i, line in enumerate(lines) if _PATCH_RE.match(line)), None)
    if patch_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        patch_index = len(lines)
        lines.append("patch:")
    patch_indent = len(_PATCH_RE.match(lines[patch_index]).group("indent"))
    child_indent = patch_indent + 2
    patch_end = len(lines)
    for index in range(patch_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(lines[index]) - len(lines[index].lstrip(" "))
        if indent <= patch_indent:
            patch_end = index
            break

    entries: list[tuple[int, int, str]] = []
    starts: list[tuple[int, str]] = []
    for index in range(patch_index + 1, patch_end):
        line = lines[index]
        if len(line) - len(line.lstrip(" ")) != child_indent:
            continue
        match = re.match(r"^\s*(?P<key>(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^:#][^:]*?))\s*:\s*", line)
        if not match:
            continue
        raw_key = match.group("key").strip()
        try:
            key = next(iter(yaml.safe_load(f"{{{raw_key}: null}}")))
        except Exception:
            key = raw_key.strip("\"'")
        starts.append((index, str(key)))
    for start, key in starts:
        end = start + 1
        while end < patch_end:
            line = lines[end]
            stripped = line.strip()
            if not stripped:
                break
            indent = len(line) - len(line.lstrip(" "))
            if indent <= child_indent:
                break
            end += 1
        entries.append((start, end, key))

    replacement = dict(updates)
    chunks: list[str] = []
    cursor = 0
    for start, end, key in entries:
        chunks.extend(lines[cursor:start])
        if key in remove_keys or any(key.startswith(prefix) for prefix in remove_prefixes):
            pass
        elif key in replacement:
            chunks.append(_render_patch_line(key, replacement.pop(key), child_indent))
        else:
            chunks.extend(lines[start:end])
        cursor = end
    chunks.extend(lines[cursor:patch_end])
    insertion = [_render_patch_line(key, value, child_indent) for key, value in replacement.items()]
    chunks.extend(insertion)
    chunks.extend(lines[patch_end:])
    return newline.join(chunks).rstrip() + newline


def _render_patch_line(key: str, value: Any, indent: int) -> str:
    rendered_key = yaml.safe_dump(str(key), allow_unicode=True, default_style='"').strip()
    rendered_value = yaml.safe_dump(
        value, allow_unicode=True, default_flow_style=True, sort_keys=False,
        width=1_000_000,
    ).strip()
    if rendered_value.endswith("\n..."):
        rendered_value = rendered_value[:-4]
    return f"{' ' * indent}{rendered_key}: {rendered_value}"
