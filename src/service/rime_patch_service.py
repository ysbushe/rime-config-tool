"""Transactional writer for small, explicit Rime custom-file patches."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.service.backup_service import BackupService
from src.service.file_transaction import (
    FileChange,
    FileTransactionConflict,
    FileRevision,
    TransactionalFileWriter,
    capture_revision,
)


class RimePatchService:
    def __init__(self, rime_dir: str, backup: BackupService,
                 writer: TransactionalFileWriter) -> None:
        self.root = Path(rime_dir)
        self.backup = backup
        self.writer = writer

    def read(self, filename: str) -> tuple[dict[str, Any], FileRevision]:
        path = self._target(filename)
        revision = capture_revision(path)
        if not revision.exists:
            return {"patch": {}}, revision
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RuntimeError(f"无法安全解析 {filename}：{exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"{filename} 顶层结构不是 YAML 映射")
        patch = value.get("patch")
        if patch is None:
            value["patch"] = {}
        elif not isinstance(patch, dict):
            raise RuntimeError(f"{filename} 的 patch 节点不是映射")
        return value, revision

    def save_updates(self, filename: str, updates: dict[str, Any],
                     expected: FileRevision) -> Path:
        path = self._target(filename)
        value, _current = self.read(filename)
        patch = value.setdefault("patch", {})
        for key, item in updates.items():
            patch[str(key)] = item
        payload = yaml.safe_dump(
            value, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=4096,
        ).encode("utf-8")
        if capture_revision(path) != expected:
            raise FileTransactionConflict(
                f"文件已被其他程序修改，本次未写入：{path}"
            )
        if expected.exists and self.backup.backup(filename) is None:
            raise RuntimeError(f"{filename} 备份失败，已停止保存")
        self.writer.commit([FileChange(path, payload, expected)])
        return path

    def _target(self, filename: str) -> Path:
        if not self.root.is_dir():
            raise RuntimeError("Rime 目录无效")
        path = (self.root / filename).resolve()
        root = self.root.resolve()
        if path.parent != root:
            raise RuntimeError("拒绝访问 Rime 目录之外的配置文件")
        return path
