"""备份服务（BackupService）。

铁律：任何写文件前必走 BackupService。备份落点：
    发布版：<用户文档>/RIME 配置小工具/Backups/<filename>/<filename>_<YYYYMMDD_HHMMSS>.bak
    源码运行：<rime_dir>/.backup/<filename>/<filename>_<YYYYMMDD_HHMMSS>.bak
每文件独立轮转，保留近 N 份（默认 5）。

历史备份兼容：同目录的旧短时间戳文件，以及旧版平铺保存的
``<filename>.*.bak`` 文件，均可列出、恢复和参与轮转。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from shutil import copy2
from dataclasses import dataclass
from typing import Iterable, List, Optional

from src.config.paths import BACKUP_DIR_NAME, DEFAULT_BACKUP_KEEP
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BackupReport:
    saved: dict[str, Optional[Path]]
    removed: tuple[Path, ...]


class BackupService:
    """为 Rime 目录下的文件提供写前备份与轮转。"""

    def __init__(self, rime_dir: str, keep: int = DEFAULT_BACKUP_KEEP,
                 backup_dir: str = "") -> None:
        self._rime_dir = Path(rime_dir)
        self._keep = max(1, int(keep))
        self._auto_cleanup = True
        self._last_removed: list[Path] = []
        self._custom_backup_dir = Path(backup_dir) if backup_dir else None
        self._backup_dir = self._custom_backup_dir or self._default_backup_dir()

    # ------------------------------------------------------------------ #
    @property
    def rime_dir(self) -> Path:
        return self._rime_dir

    @rime_dir.setter
    def rime_dir(self, value: str) -> None:
        self._rime_dir = Path(value)
        if self._custom_backup_dir is None:
            self._backup_dir = self._default_backup_dir()

    def _default_backup_dir(self) -> Path:
        """Release builds store backups in Documents; source runs keep project-local behavior."""
        if getattr(sys, "frozen", False):
            return Path.home() / "Documents" / "RIME 配置小工具" / "Backups"
        return self._rime_dir / BACKUP_DIR_NAME

    @property
    def using_default_backup_dir(self) -> bool:
        return self._custom_backup_dir is None

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    @backup_dir.setter
    def backup_dir(self, value: str) -> None:
        self._custom_backup_dir = Path(value) if value else None
        self._backup_dir = self._custom_backup_dir or self._default_backup_dir()

    @property
    def auto_cleanup(self) -> bool:
        return self._auto_cleanup

    @auto_cleanup.setter
    def auto_cleanup(self, value: bool) -> None:
        self._auto_cleanup = bool(value)

    @property
    def keep(self) -> int:
        return self._keep

    @keep.setter
    def keep(self, value: int) -> None:
        self._keep = max(1, int(value))

    # ------------------------------------------------------------------ #
    # 备份
    # ------------------------------------------------------------------ #
    def backup(self, filename: str) -> Optional[Path]:
        """写文件前调用：复制当前文件到 .backup，并轮转。

        返回备份文件路径；若源文件不存在（首次创建）返回 None。
        """
        src = self._rime_dir / filename
        if not src.exists():
            logger.info("源文件不存在，跳过备份：%s", src)
            return None

        # Keep each managed file in its own folder and retain its source name
        # in the backup file, so the backup remains recognizable if copied out.
        target_dir = self._backup_dir / filename
        target_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = target_dir / f"{filename}_{ts}.bak"
        suffix = 2
        while dest.exists():
            dest = target_dir / f"{filename}_{ts}_{suffix}.bak"
            suffix += 1
        try:
            copy2(src, dest)
            logger.info("已备份：%s -> %s", src, dest)
        except Exception as exc:
            logger.warning("备份失败：%s", exc)
            return None
        self._last_removed = self._rotate(filename) if self._auto_cleanup else []
        return dest

    def backup_files(self, filenames: Iterable[str]) -> dict[str, Optional[Path]]:
        """Back up a managed set once, returning an entry for every file."""
        return {filename: self.backup(filename) for filename in filenames}

    def backup_files_report(self, filenames: Iterable[str]) -> BackupReport:
        saved: dict[str, Optional[Path]] = {}
        removed: list[Path] = []
        for filename in filenames:
            saved[filename] = self.backup(filename)
            removed.extend(self._last_removed)
        return BackupReport(saved=saved, removed=tuple(removed))

    def _rotate(self, filename: str) -> list[Path]:
        """保留最近 keep 份，删除更早的。"""
        backups = list(reversed(self.list_backups(filename)))
        excess = len(backups) - self._keep
        # 关键：文件数未超过保留份数（excess <= 0）时绝不删除，
        # 否则 Python 切片 backups[:-1] 会误删除最后一份外的所有文件。
        if excess <= 0:
            return []
        removed: list[Path] = []
        for old in backups[:excess]:
            try:
                old.unlink()
                removed.append(old)
                logger.debug("清理旧备份：%s", old)
            except OSError:
                pass
        return removed

    # ------------------------------------------------------------------ #
    # 回滚辅助
    # ------------------------------------------------------------------ #
    def list_backups(self, filename: str) -> List[Path]:
        """返回某文件的所有备份（最新在前），兼容历史命名。"""
        # Nested files cover both the new ``filename_YYYYMMDD_HHMMSS.bak``
        # form and all former timestamp-only names. The flat pattern preserves
        # compatibility with releases that stored backups directly at root.
        nested = (self._backup_dir / filename).glob("*.bak")
        legacy_flat = self._backup_dir.glob(f"{filename}.*.bak")
        return sorted(
            [*nested, *legacy_flat],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def restore(self, backup_path: str, filename: str) -> bool:
        """从备份恢复（恢复前建议再备份当前版本）。"""
        src = Path(backup_path)
        if not src.is_file():
            return False
        # Restore only from a recognized backup of this managed file. This
        # keeps the new and legacy naming rules safe without trusting a path
        # passed from outside the backup list.
        known_paths = {path.resolve() for path in self.list_backups(filename)}
        if src.resolve() not in known_paths:
            return False
        copy2(src, self._rime_dir / filename)
        logger.info("已从备份恢复：%s -> %s", src, self._rime_dir / filename)
        return True
