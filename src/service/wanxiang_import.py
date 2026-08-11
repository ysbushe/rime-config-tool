"""Lossless White Frost custom-phrase import for Wanxiang full pinyin.

The existing phrase editor intentionally normalizes display pinyin and rewrites
the complete file.  Migration must not use that path: apostrophes, two-column
rows, unusual weights, comments and line endings are meaningful user data.
This module therefore works on a tokenized byte-preserving document and only
appends accepted source entries to a dedicated Wanxiang user dictionary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Callable, Iterable, Literal
from uuid import uuid4

import yaml


SOURCE_FILENAME = "custom_phrase.txt"
TARGET_FILENAME = "wanxiang_user_phrase.txt"
WANXIANG_SCHEMA_FILENAME = "wanxiang.schema.yaml"
WANXIANG_CUSTOM_FILENAME = "wanxiang.custom.yaml"
TARGET_USER_DICT = "wanxiang_user_phrase"


class PhraseFormatError(ValueError):
    """The selected source is not a supported tab-separated phrase table."""


class ImportExecutionError(RuntimeError):
    """The import could not be committed or verified and was rolled back."""


@dataclass(frozen=True)
class FileFingerprint:
    exists: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""


@dataclass(frozen=True)
class PhraseEntry:
    line_number: int
    raw: str
    text: str
    code: str
    weight_raw: str | None
    weight_value: int | None

    @property
    def key(self) -> tuple[str, str]:
        return self.text, self.code


@dataclass(frozen=True)
class PhraseLine:
    kind: Literal["header", "comment", "blank", "entry", "unknown"]
    raw: str
    line_number: int
    entry: PhraseEntry | None = None


@dataclass
class PhraseDocument:
    lines: list[PhraseLine]
    newline: str = "\n"
    has_final_newline: bool = True
    has_bom: bool = False

    @property
    def entries(self) -> list[PhraseEntry]:
        return [line.entry for line in self.lines if line.entry is not None]

    @property
    def unknown_lines(self) -> list[PhraseLine]:
        return [line for line in self.lines if line.kind == "unknown"]

    def render(self) -> bytes:
        text = self.newline.join(line.raw for line in self.lines)
        if self.has_final_newline and self.lines:
            text += self.newline
        payload = text.encode("utf-8")
        return (b"\xef\xbb\xbf" + payload) if self.has_bom else payload

    def append_entries(self, entries: Iterable[PhraseEntry]) -> None:
        next_line = len(self.lines) + 1
        for entry in entries:
            self.lines.append(PhraseLine("entry", entry.raw, next_line, entry))
            next_line += 1
        if self.lines:
            self.has_final_newline = True

    def replace_entry(self, entry: PhraseEntry) -> bool:
        """Replace one target row while retaining its original file position."""
        for index, line in enumerate(self.lines):
            if line.entry is None or line.entry.key != entry.key:
                continue
            replacement = PhraseEntry(
                line.line_number, entry.raw, entry.text, entry.code,
                entry.weight_raw, entry.weight_value,
            )
            self.lines[index] = PhraseLine(
                "entry", entry.raw, line.line_number, replacement,
            )
            return True
        return False


@dataclass(frozen=True)
class CompatibilityWarning:
    line_number: int
    kind: str
    message: str


@dataclass(frozen=True)
class ImportConflict:
    source: PhraseEntry
    target: PhraseEntry


@dataclass
class ImportPlan:
    source_path: Path
    target_path: Path
    patch_path: Path | None
    fingerprints: dict[Path, FileFingerprint]
    target_bytes: bytes
    patch_bytes: bytes | None
    source_count: int
    import_count: int
    skipped_exact: int
    skipped_different: int
    warnings: list[CompatibilityWarning] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)

    @property
    def can_execute(self) -> bool:
        return not self.blockers and self.import_count > 0


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped_exact: int
    skipped_different: int
    snapshot_dir: Path
    deploy_message: str


def fingerprint(path: Path) -> FileFingerprint:
    if not path.is_file():
        return FileFingerprint(False)
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return FileFingerprint(True, stat.st_size, stat.st_mtime_ns, digest)


def parse_phrase_bytes(raw: bytes, *, source_name: str = "词库") -> PhraseDocument:
    """Parse UTF-8 phrase text while retaining every original row verbatim."""
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise PhraseFormatError(f"{source_name} 使用 UTF-16；请先转为 UTF-8。")
    payload = raw[3:] if has_bom else raw
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhraseFormatError(f"{source_name} 不是有效的 UTF-8 文件。") from exc

    if re.search(r"(?m)^\s*---\s*$", text) and re.search(
            r"(?m)^\s*(name|version|sort|columns)\s*:", text):
        raise PhraseFormatError(f"{source_name} 是 .dict.yaml 固定词典，不是自定义短语表。")

    newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text else "\n")
    has_final_newline = text.endswith(("\n", "\r"))
    raw_lines = text.splitlines()
    lines: list[PhraseLine] = []
    seen_entry = False
    comma_rows = 0
    for number, raw_line in enumerate(raw_lines, 1):
        if not raw_line.strip():
            lines.append(PhraseLine("blank", raw_line, number))
            continue
        if raw_line.startswith("#"):
            kind = "header" if not seen_entry else "comment"
            lines.append(PhraseLine(kind, raw_line, number))
            continue
        parts = raw_line.split("\t")
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            if "\t" not in raw_line and "," in raw_line:
                comma_rows += 1
            lines.append(PhraseLine("unknown", raw_line, number))
            continue
        weight_raw = parts[2] if len(parts) == 3 else None
        try:
            weight_value = int(weight_raw) if weight_raw not in (None, "") else None
        except ValueError:
            weight_value = None
        entry = PhraseEntry(
            line_number=number,
            raw=raw_line,
            text=parts[0],
            code=parts[1],
            weight_raw=weight_raw,
            weight_value=weight_value,
        )
        seen_entry = True
        lines.append(PhraseLine("entry", raw_line, number, entry))

    if comma_rows and not any(line.entry for line in lines):
        raise PhraseFormatError(f"{source_name} 看起来是 CSV；本迁移只接受 Tab 分隔的自定义短语表。")
    return PhraseDocument(lines, newline, has_final_newline, has_bom)


def parse_phrase_file(path: Path, *, source_name: str = "词库") -> PhraseDocument:
    if not path.is_file():
        raise PhraseFormatError(f"未找到{source_name}：{path.name}")
    return parse_phrase_bytes(path.read_bytes(), source_name=source_name)


def scan_compatibility(entries: Iterable[PhraseEntry]) -> list[CompatibilityWarning]:
    warnings: list[CompatibilityWarning] = []
    for entry in entries:
        code = entry.code
        if "'" in code or "’" in code:
            warnings.append(CompatibilityWarning(
                entry.line_number, "pinyin_delimiter",
                "编码含音节分隔符；将原样保留，请确认万象全拼分隔符设置。",
            ))
        elif not re.fullmatch(r"[a-z]+", code):
            warnings.append(CompatibilityWarning(
                entry.line_number, "special_code",
                "编码不是纯小写全拼，可能触发万象的反查、快捷键或识别器规则。",
            ))
        if re.search(r"\\(?:Y|n|s)", entry.text) or "[[" in entry.text:
            warnings.append(CompatibilityWarning(
                entry.line_number, "dynamic_text",
                "文本含万象动态短语控制序列，导入后可能不会按字面输出。",
            ))
        if entry.weight_raw is not None and entry.weight_raw != "" \
                and entry.weight_value is None:
            warnings.append(CompatibilityWarning(
                entry.line_number, "non_numeric_weight",
                "权重不是整数；将原样保留，Rime 可能忽略或拒绝该行。",
            ))
    return warnings


def _yaml_mapping(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PhraseFormatError(f"无法解析 {path.name}：{exc}") from exc
    return data if isinstance(data, dict) else {}


def _schema_user_dict(schema: dict) -> str:
    custom = schema.get("custom_phrase", {})
    if isinstance(custom, dict):
        return str(custom.get("user_dict", ""))
    return ""


_PATCH_KEY_RE = re.compile(
    r'^(?P<indent>[ \t]+)(?P<quote>["\']?)custom_phrase/user_dict(?P=quote)'
    r'[ \t]*:[ \t]*(?P<value>[^#\r\n]*?)(?P<comment>[ \t]+#.*)?'
    r'(?P<eol>\r?)$',
    re.MULTILINE,
)


def build_wanxiang_patch(raw: bytes) -> tuple[bytes, str | None]:
    """Return patched bytes and an optional blocking conflict message."""
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    payload = raw[3:] if has_bom else raw
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhraseFormatError("wanxiang.custom.yaml 不是有效的 UTF-8 文件。") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    match = _PATCH_KEY_RE.search(text)
    if match:
        current = match.group("value").strip().strip('"\'')
        if current not in ("", "custom_phrase", TARGET_USER_DICT):
            return raw, f"万象已使用自定义词库 {current}，不会在未确认时覆盖。"
        replacement = (
            f'{match.group("indent")}"custom_phrase/user_dict": {TARGET_USER_DICT}'
            f'{match.group("comment") or ""}{match.group("eol") or ""}'
        )
        text = text[:match.start()] + replacement + text[match.end():]
    else:
        patch_match = re.search(
            r"(?m)^patch[ \t]*:[ \t]*(?:#.*)?(?P<eol>\r?)$", text
        )
        if patch_match:
            insertion_at = patch_match.start("eol")
            insertion = f'{newline}  "custom_phrase/user_dict": {TARGET_USER_DICT}'
            text = text[:insertion_at] + insertion + text[insertion_at:]
        elif re.search(r"(?m)^patch\s*:\s*\{", text):
            return raw, "wanxiang.custom.yaml 使用行内 patch，当前版本不会自动改写。"
        else:
            if text and not text.endswith(("\n", "\r")):
                text += newline
            text += f'patch:{newline}  "custom_phrase/user_dict": {TARGET_USER_DICT}{newline}'
    encoded = text.encode("utf-8")
    return ((b"\xef\xbb\xbf" + encoded) if has_bom else encoded), None


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class WanxiangImportService:
    """Plan and commit a one-way import into a dedicated Wanxiang phrase file."""

    def __init__(self, rime_dir: str | Path, backup_root: str | Path,
                 deploy: Callable[[], tuple[bool, str]]) -> None:
        self.rime_dir = Path(rime_dir)
        self.backup_root = Path(backup_root)
        self._deploy = deploy
        self.source_name = "白霜拼音"
        self.target_name = "万象拼音"
        self.requires_wanxiang = True

    @property
    def source_path(self) -> Path:
        return self.rime_dir / SOURCE_FILENAME

    @property
    def target_path(self) -> Path:
        return self.rime_dir / TARGET_FILENAME

    @property
    def patch_path(self) -> Path:
        return self.rime_dir / WANXIANG_CUSTOM_FILENAME

    def plan(self, overwrite_keys: set[tuple[str, str]] | None = None) -> ImportPlan:
        overwrite_keys = set(overwrite_keys or ())
        blockers: list[str] = []
        schema_path = self.rime_dir / WANXIANG_SCHEMA_FILENAME
        if not schema_path.is_file():
            raise PhraseFormatError("未检测到 wanxiang.schema.yaml，无法确认万象拼音已安装。")
        schema = _yaml_mapping(schema_path)
        configured_dict = _schema_user_dict(schema)
        if not configured_dict:
            blockers.append("万象方案未检测到 custom_phrase/user_dict 配置。")

        source = parse_phrase_file(self.source_path, source_name="白霜自定义词库")
        if source.unknown_lines:
            nums = "、".join(str(line.line_number) for line in source.unknown_lines[:8])
            blockers.append(f"来源包含无法识别的行：{nums}；为保证无损，本次不会跳过后继续。")
        if not source.entries:
            blockers.append("来源词库没有可导入的条目。")

        if self.target_path.exists():
            target = parse_phrase_file(self.target_path, source_name="万象目标词库")
        else:
            target = PhraseDocument([
                PhraseLine("header", "# Rime custom phrases for Wanxiang", 1),
                PhraseLine("header", "# Managed by RIME 配置小工具; source remains unchanged.", 2),
            ], source.newline, True, source.has_bom)

        existing = {entry.key: entry for entry in target.entries}
        accepted: list[PhraseEntry] = []
        conflicts: list[ImportConflict] = []
        skipped_exact = 0
        skipped_different = 0
        for entry in source.entries:
            current = existing.get(entry.key)
            if current is not None:
                if current.raw == entry.raw:
                    skipped_exact += 1
                else:
                    conflicts.append(ImportConflict(entry, current))
                    if entry.key in overwrite_keys and target.replace_entry(entry):
                        accepted.append(entry)
                        existing[entry.key] = entry
                    else:
                        skipped_different += 1
                continue
            existing[entry.key] = entry
            accepted.append(entry)
        target.append_entries(accepted)

        patch_raw = self.patch_path.read_bytes() if self.patch_path.is_file() else b""
        patch_bytes, conflict = build_wanxiang_patch(patch_raw)
        if conflict:
            blockers.append(conflict)

        watched = (self.source_path, self.target_path, self.patch_path, schema_path)
        return ImportPlan(
            source_path=self.source_path,
            target_path=self.target_path,
            patch_path=self.patch_path,
            fingerprints={path: fingerprint(path) for path in watched},
            target_bytes=target.render(),
            patch_bytes=patch_bytes,
            source_count=len(source.entries),
            import_count=len(accepted),
            skipped_exact=skipped_exact,
            skipped_different=skipped_different,
            warnings=scan_compatibility(accepted),
            blockers=blockers,
            conflicts=conflicts,
        )

    def execute(self, plan: ImportPlan, *, verify_timeout: float = 30.0) -> ImportResult:
        if plan.blockers:
            raise ImportExecutionError("；".join(plan.blockers))
        if plan.import_count <= 0:
            raise ImportExecutionError("没有需要写入的新条目。")
        for path, expected in plan.fingerprints.items():
            if fingerprint(path) != expected:
                raise ImportExecutionError(f"{path.name} 在预览后发生变化，请重新扫描。")

        snapshot = self._snapshot((plan.target_path, plan.patch_path))
        written = {plan.target_path: plan.target_bytes, plan.patch_path: plan.patch_bytes}
        try:
            _atomic_write_bytes(plan.target_path, plan.target_bytes)
            assert plan.patch_path is not None and plan.patch_bytes is not None
            _atomic_write_bytes(plan.patch_path, plan.patch_bytes)
            if plan.target_path.read_bytes() != plan.target_bytes:
                raise ImportExecutionError("写入后校验失败：目标词库内容不一致。")
            reparsed = parse_phrase_file(plan.target_path, source_name="写入后的万象目标词库")
            if len(reparsed.entries) < plan.import_count:
                raise ImportExecutionError("写入后校验失败：目标条目数异常。")
            patch_raw = plan.patch_path.read_bytes()
            patched, conflict = build_wanxiang_patch(patch_raw)
            if conflict or patched != patch_raw:
                raise ImportExecutionError("写入后校验失败：万象挂载配置未生效。")

            ok, message = self._deploy()
            if not ok:
                raise ImportExecutionError(message)
            if verify_timeout > 0 and not self._wait_for_build(verify_timeout):
                raise ImportExecutionError("部署完成但未验证到万象方案挂载新词库。")
            if fingerprint(plan.source_path) != plan.fingerprints[plan.source_path]:
                raise ImportExecutionError("来源词库在导入期间发生变化，目标变更将回滚。")
            if (plan.target_path.read_bytes() != plan.target_bytes
                    or plan.patch_path.read_bytes() != plan.patch_bytes):
                raise ImportExecutionError("目标文件在部署期间被外部修改。")
            return ImportResult(
                plan.import_count,
                plan.skipped_exact,
                plan.skipped_different,
                snapshot,
                message,
            )
        except Exception as exc:
            conflicts = self._restore_snapshot(snapshot, written)
            if conflicts:
                names = "、".join(path.name for path in conflicts)
                raise ImportExecutionError(
                    f"{exc}；检测到外部修改，未覆盖 {names}。请使用事务快照人工核对：{snapshot}"
                ) from exc
            try:
                self._deploy()
            except Exception:
                pass
            if isinstance(exc, ImportExecutionError):
                raise
            raise ImportExecutionError(f"导入失败，已恢复原文件：{exc}") from exc

    def _snapshot(self, paths: Iterable[Path]) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = self.backup_root / "migrations" / f"{stamp}_{uuid4().hex[:8]}"
        try:
            root.mkdir(parents=True, exist_ok=False)
            manifest: dict[str, bool] = {}
            for path in paths:
                manifest[path.name] = path.is_file()
                if path.is_file():
                    shutil.copy2(path, root / path.name)
            (root / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            raise ImportExecutionError(f"无法创建迁移快照，未写入任何文件：{exc}") from exc
        return root

    @staticmethod
    def _restore_snapshot(snapshot: Path, written: dict[Path, bytes]) -> list[Path]:
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        conflicts: list[Path] = []
        for path, expected_current in written.items():
            if path.is_file() and path.read_bytes() != expected_current:
                conflicts.append(path)
                continue
            if manifest.get(path.name):
                _atomic_write_bytes(path, (snapshot / path.name).read_bytes())
            else:
                path.unlink(missing_ok=True)
        return conflicts

    def _wait_for_build(self, timeout: float) -> bool:
        built = self.rime_dir / "build" / WANXIANG_SCHEMA_FILENAME
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if built.is_file():
                try:
                    if _schema_user_dict(_yaml_mapping(built)) == TARGET_USER_DICT:
                        return True
                except PhraseFormatError:
                    pass
            time.sleep(0.25)
        return False


class DirectSchemeImportService:
    """Copy missing phrase rows between two already-mounted personal dictionaries.

    This is used for the reverse Wanxiang -> White Frost direction.  It never
    changes either schema's mount configuration: the destination dictionary is
    already the active scheme's managed phrase file.
    """

    requires_wanxiang = False

    def __init__(self, rime_dir: str | Path, backup_root: str | Path,
                 deploy: Callable[[], tuple[bool, str]], *,
                 source_filename: str, target_filename: str,
                 source_name: str, target_name: str) -> None:
        self.rime_dir = Path(rime_dir)
        self.backup_root = Path(backup_root)
        self._deploy = deploy
        self.source_path = self.rime_dir / source_filename
        self.target_path = self.rime_dir / target_filename
        self.source_name = source_name
        self.target_name = target_name

    def plan(self, overwrite_keys: set[tuple[str, str]] | None = None) -> ImportPlan:
        overwrite_keys = set(overwrite_keys or ())
        source = parse_phrase_file(self.source_path, source_name=f"{self.source_name}个人词库")
        target = parse_phrase_file(self.target_path, source_name=f"{self.target_name}个人词库")
        blockers: list[str] = []
        if source.unknown_lines:
            nums = "、".join(str(line.line_number) for line in source.unknown_lines[:8])
            blockers.append(f"来源包含无法识别的行：{nums}；为保证无损，本次不会跳过后继续。")
        if not source.entries:
            blockers.append("来源词库没有可导入的条目。")
        existing = {entry.key: entry for entry in target.entries}
        accepted: list[PhraseEntry] = []
        conflicts: list[ImportConflict] = []
        skipped_exact = 0
        skipped_different = 0
        for entry in source.entries:
            current = existing.get(entry.key)
            if current is not None:
                if current.raw == entry.raw:
                    skipped_exact += 1
                else:
                    conflicts.append(ImportConflict(entry, current))
                    if entry.key in overwrite_keys and target.replace_entry(entry):
                        accepted.append(entry)
                        existing[entry.key] = entry
                    else:
                        skipped_different += 1
                continue
            existing[entry.key] = entry
            accepted.append(entry)
        target.append_entries(accepted)
        watched = (self.source_path, self.target_path)
        return ImportPlan(
            source_path=self.source_path,
            target_path=self.target_path,
            patch_path=None,
            fingerprints={path: fingerprint(path) for path in watched},
            target_bytes=target.render(),
            patch_bytes=None,
            source_count=len(source.entries),
            import_count=len(accepted),
            skipped_exact=skipped_exact,
            skipped_different=skipped_different,
            warnings=scan_compatibility(accepted),
            blockers=blockers,
            conflicts=conflicts,
        )

    def execute(self, plan: ImportPlan, *, verify_timeout: float = 30.0) -> ImportResult:
        del verify_timeout
        if plan.blockers:
            raise ImportExecutionError("；".join(plan.blockers))
        if plan.import_count <= 0:
            raise ImportExecutionError("没有需要写入的新条目。")
        for path, expected in plan.fingerprints.items():
            if fingerprint(path) != expected:
                raise ImportExecutionError(f"{path.name} 在预览后发生变化，请重新扫描。")
        snapshot = self._snapshot((plan.target_path,))
        written = {plan.target_path: plan.target_bytes}
        try:
            _atomic_write_bytes(plan.target_path, plan.target_bytes)
            if plan.target_path.read_bytes() != plan.target_bytes:
                raise ImportExecutionError("写入后校验失败：目标词库内容不一致。")
            parse_phrase_file(plan.target_path, source_name=f"写入后的{self.target_name}目标词库")
            ok, message = self._deploy()
            if not ok:
                raise ImportExecutionError(message)
            if fingerprint(plan.source_path) != plan.fingerprints[plan.source_path]:
                raise ImportExecutionError("来源词库在导入期间发生变化，目标变更将回滚。")
            if plan.target_path.read_bytes() != plan.target_bytes:
                raise ImportExecutionError("目标文件在部署期间被外部修改。")
            return ImportResult(
                plan.import_count, plan.skipped_exact, plan.skipped_different,
                snapshot, message,
            )
        except Exception as exc:
            conflicts = self._restore_snapshot(snapshot, written)
            if conflicts:
                raise ImportExecutionError(
                    f"{exc}；检测到外部修改，未覆盖目标文件。请核对事务快照：{snapshot}"
                ) from exc
            try:
                self._deploy()
            except Exception:
                pass
            if isinstance(exc, ImportExecutionError):
                raise
            raise ImportExecutionError(f"导入失败，已恢复原文件：{exc}") from exc

    def _snapshot(self, paths: Iterable[Path]) -> Path:
        return WanxiangImportService._snapshot(self, paths)

    @staticmethod
    def _restore_snapshot(snapshot: Path, written: dict[Path, bytes]) -> list[Path]:
        return WanxiangImportService._restore_snapshot(snapshot, written)
