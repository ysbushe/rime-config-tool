"""UI-independent staged editing for one safe personal text dictionary."""
from __future__ import annotations

from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.backup_service import BackupService
from src.service.dictionary_catalog import DictionaryCatalogItem, input_code
from src.service.file_transaction import (
    FileChange,
    FileRevision,
    TransactionalFileWriter,
    capture_revision,
)


class DictionaryEditSession:
    """Own the mutable copy, validation and explicit-save boundary."""

    def __init__(
        self,
        item: DictionaryCatalogItem,
        transaction_writer: TransactionalFileWriter | None = None,
        backup_service: BackupService | None = None,
    ) -> None:
        if not self.can_edit(item):
            raise ValueError("该词典不能在词典中心直接编辑")
        self.item = item
        self.transaction_writer = transaction_writer
        self.backup_service = backup_service
        self.repo: PhraseRepo
        self.entries: list[Phrase]
        self.revision: FileRevision
        self.dirty = False
        self.reload()

    @staticmethod
    def can_edit(item: DictionaryCatalogItem) -> bool:
        # custom_phrase.txt has group/display sidecars and remains in the
        # dedicated personal-library workbench.
        return bool(
            item.editable and len(item.entry_paths) == 1
            and item.entry_paths[0].suffix.casefold() == ".txt"
            and item.entry_paths[0].name.casefold() != "custom_phrase.txt"
        )

    def reload(self) -> None:
        self.repo = PhraseRepo(str(self.item.entry_paths[0]))
        self.entries = self.repo.all()
        self.revision = capture_revision(self.item.path)
        self.dirty = False

    def visible_entries(self, query: str) -> list[tuple[int, Phrase]]:
        needle = query.strip().casefold()
        return [
            (index, entry)
            for index, entry in enumerate(self.entries)
            if not needle or needle in {
                entry.text.casefold(), input_code(entry.code).casefold(),
            }
        ]

    def update(self, index: int, column: int, value: str) -> str:
        if not 0 <= index < len(self.entries):
            return "词条已经变化，请重新选择。"
        entry = self.entries[index]
        if column == 0:
            entry.text = value.strip()
        elif column == 1:
            entry.code = input_code(value)
        elif column == 2:
            try:
                entry.weight = max(1, min(99999999, int(value)))
            except ValueError:
                return "权重必须是正整数。"
        else:
            return ""
        self.dirty = True
        return ""

    def add(self) -> int:
        self.entries.append(Phrase("", "", 1))
        self.dirty = True
        return len(self.entries) - 1

    def delete(self, index: int) -> bool:
        if not 0 <= index < len(self.entries):
            return False
        self.entries.pop(index)
        self.dirty = True
        return True

    def validate(self) -> str:
        identities: set[tuple[str, str]] = set()
        for entry in self.entries:
            entry.text = entry.text.strip()
            entry.code = input_code(entry.code)
            if not entry.text or not entry.code:
                return "字词和拼音编码均不能为空。"
            identity = (entry.text, entry.code)
            if identity in identities:
                return f"存在重复词条：{entry.text} / {entry.code}"
            identities.add(identity)
        return ""

    def save(self) -> int:
        error = self.validate()
        if error:
            raise ValueError(error)
        self.repo.replace_all(self.entries)
        if self.backup_service is not None:
            self.backup_service.backup(self.item.path.name)
        if self.transaction_writer is not None:
            self.transaction_writer.commit([FileChange(
                self.item.path,
                self.repo.serialized_text().encode("utf-8"),
                self.revision,
            )])
            self.repo.mark_saved()
        else:
            self.repo.save()
        self.revision = capture_revision(self.item.path)
        self.dirty = False
        return len(self.entries)
