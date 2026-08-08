"""Prepare an isolated, read-mostly Rime workspace for candidate previews."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4


_RUNTIME_DIRS = ("lua", "opencc", "custom", "others")
_TOP_LEVEL_SUFFIXES = (".yaml", ".txt", ".json")
_MANIFEST = ".rime-config-preview.json"


@dataclass(frozen=True)
class PreviewWorkspaceReport:
    path: Path
    copied_userdbs: tuple[str, ...]
    skipped_userdbs: tuple[str, ...]


class PreviewWorkspace:
    """Mirror only runtime inputs; all librime writes stay inside the mirror."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def prepare(self, source: str | Path, schema_id: str) -> PreviewWorkspaceReport:
        source_root = Path(source).resolve()
        if not source_root.is_dir():
            raise RuntimeError("真实 Rime 目录不存在，无法准备隔离预览。")
        schema = "".join(char for char in schema_id if char.isalnum() or char in "_-")
        if not schema:
            raise RuntimeError("预览方案 ID 无效。")
        destination = self.root / schema
        signature = self._signature(source_root)
        current = self._read_manifest(destination)
        if current.get("source") == str(source_root) and current.get("signature") == signature:
            return PreviewWorkspaceReport(
                destination,
                tuple(current.get("copied_userdbs") or ()),
                tuple(current.get("skipped_userdbs") or ()),
            )

        staging = self.root / f".{schema}-{uuid4().hex}.preparing"
        staging.mkdir(parents=True, exist_ok=False)
        copied_userdbs: list[str] = []
        skipped_userdbs: list[str] = []
        try:
            self._copy_top_level(source_root, staging)
            for dirname in _RUNTIME_DIRS:
                source_dir = source_root / dirname
                if source_dir.is_dir():
                    self._link_tree(source_dir, staging / dirname)
            for userdb in sorted(source_root.glob("*.userdb")):
                if not userdb.is_dir():
                    continue
                if self._copy_userdb(userdb, staging / userdb.name):
                    copied_userdbs.append(userdb.name)
                else:
                    skipped_userdbs.append(userdb.name)
            (staging / "build").mkdir(exist_ok=True)
            manifest = {
                "version": 1,
                "source": str(source_root),
                "signature": signature,
                "copied_userdbs": copied_userdbs,
                "skipped_userdbs": skipped_userdbs,
            }
            (staging / _MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            if destination.is_dir():
                shutil.rmtree(destination)
            os.replace(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return PreviewWorkspaceReport(
            destination, tuple(copied_userdbs), tuple(skipped_userdbs),
        )

    @staticmethod
    def _copy_top_level(source: Path, destination: Path) -> None:
        for path in source.iterdir():
            if not path.is_file():
                continue
            name = path.name.casefold()
            if name.endswith(_TOP_LEVEL_SUFFIXES):
                shutil.copy2(path, destination / path.name)
            elif name.endswith(".gram"):
                PreviewWorkspace._link_or_copy(path, destination / path.name)

    @staticmethod
    def _link_tree(source: Path, destination: Path) -> None:
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            target = destination / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                PreviewWorkspace._link_or_copy(path, target)

    @staticmethod
    def _link_or_copy(source: Path, destination: Path) -> None:
        try:
            os.link(source, destination)
        except OSError:
            shutil.copy2(source, destination)

    @staticmethod
    def _copy_userdb(source: Path, destination: Path) -> bool:
        copied = False
        destination.mkdir(parents=True, exist_ok=True)
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            target = destination / relative
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied = True
            except OSError:
                continue
        if not copied:
            shutil.rmtree(destination, ignore_errors=True)
        return copied

    @staticmethod
    def _signature(source: Path) -> list[list[object]]:
        values: list[list[object]] = []
        candidates = [
            path for path in source.iterdir()
            if path.is_file() and (
                path.name.casefold().endswith(_TOP_LEVEL_SUFFIXES)
                or path.name.casefold().endswith(".gram")
            )
        ]
        for dirname in _RUNTIME_DIRS:
            directory = source / dirname
            if directory.is_dir():
                candidates.extend(path for path in directory.rglob("*") if path.is_file())
        for directory in source.glob("*.userdb"):
            if directory.is_dir():
                candidates.extend(path for path in directory.rglob("*") if path.is_file())
        for path in sorted(set(candidates)):
            try:
                stat = path.stat()
                values.append([
                    path.relative_to(source).as_posix(), stat.st_size, stat.st_mtime_ns,
                ])
            except OSError:
                continue
        build = source / "build"
        if build.is_dir():
            try:
                newest = max(
                    (path.stat().st_mtime_ns for path in build.iterdir() if path.is_file()),
                    default=0,
                )
                values.append(["build", newest, 0])
            except OSError:
                pass
        return values

    @staticmethod
    def _read_manifest(destination: Path) -> dict:
        try:
            value = json.loads((destination / _MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
