"""Persistence boundary for the personal phrase file and its sidecars."""
from __future__ import annotations

from src.repo.phrase_repo import PhraseRepo
from src.service.backup_service import BackupService
from src.service.file_transaction import (
    FileChange,
    TransactionalFileWriter,
    capture_revision,
)
from src.service.group_service import GroupService
from src.service.pinyin_display_store import PinyinDisplayStore
from src.utils.logger import get_logger


logger = get_logger(__name__)


class PhraseBundleWriter:
    """Back up and persist related personal-library files as one unit."""

    def __init__(
        self,
        backup_service: BackupService,
        transaction_writer: TransactionalFileWriter | None = None,
    ) -> None:
        self.backup_service = backup_service
        self.transaction_writer = transaction_writer

    def backup(
        self,
        repo: PhraseRepo,
        groups: GroupService,
        display_store: PinyinDisplayStore,
    ) -> dict[str, str]:
        saved: dict[str, str] = {}
        try:
            path = self.backup_service.backup(repo.path.name)
            if path is not None:
                saved[repo.path.name] = str(path)
            path = self.backup_service.backup(display_store.path.name)
            if path is not None:
                saved[display_store.path.name] = str(path)
            if groups.enabled:
                path = self.backup_service.backup(groups.path.name)
                if path is not None:
                    saved[groups.path.name] = str(path)
        except Exception as exc:
            logger.warning("备份失败：%s", exc)
        return saved

    def save(
        self,
        repo: PhraseRepo,
        groups: GroupService,
        display_store: PinyinDisplayStore,
        *,
        include_phrase: bool = True,
        include_groups: bool = True,
        include_display: bool = True,
    ) -> None:
        writer = self.transaction_writer
        if writer is None:
            if include_phrase:
                repo.save()
            if include_groups:
                groups.save()
            if include_display:
                display_store.save()
            return

        changes = []
        if include_phrase:
            changes.append(FileChange(
                repo.path,
                repo.serialized_text().encode("utf-8"),
                capture_revision(repo.path),
            ))
        if include_groups and groups.enabled:
            changes.append(FileChange(
                groups.path,
                groups.serialized_text().encode("utf-8"),
                capture_revision(groups.path),
            ))
        if include_display:
            changes.append(FileChange(
                display_store.path,
                display_store.serialized_text().encode("utf-8"),
                capture_revision(display_store.path),
            ))
        if not changes:
            return
        writer.commit(changes)
        if include_phrase:
            repo.mark_saved()
        if include_groups:
            groups.mark_saved()
