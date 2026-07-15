"""Shared local metadata, library analysis and import/export helpers."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable

from src.config.paths import user_config_dir
from src.encoding.code_suggestions import raw_code
from src.repo.phrase_repo import Phrase
from src.utils.encoding import write_text_utf8


@dataclass(frozen=True)
class HealthIssue:
    kind: str
    message: str
    text: str = ""
    code: str = ""


@dataclass(frozen=True)
class DuplicateIndex:
    by_text: dict[str, tuple[Phrase, ...]]
    by_code: dict[str, tuple[Phrase, ...]]

    @classmethod
    def build(cls, phrases: Iterable[Phrase]) -> "DuplicateIndex":
        texts: dict[str, list[Phrase]] = defaultdict(list)
        codes: dict[str, list[Phrase]] = defaultdict(list)
        for phrase in phrases:
            texts[phrase.text].append(phrase)
            codes[raw_code(phrase.code)].append(phrase)
        return cls(
            {key: tuple(value) for key, value in texts.items() if len({raw_code(item.code) for item in value}) > 1},
            {key: tuple(value) for key, value in codes.items() if key and len({item.text for item in value}) > 1},
        )


class MetadataStore:
    """Versioned local-only metadata for notes, tags, rules and audit history."""
    VERSION = 1

    def __init__(self, rime_dir: str | Path) -> None:
        # Metadata stays outside the Rime user folder so tags and notes never affect deployment.
        identity = hashlib.sha1(str(Path(rime_dir).resolve() if rime_dir else "").encode("utf-8")).hexdigest()[:16]
        self.path = user_config_dir() / "metadata" / f"{identity}.json"
        self._data = {"version": self.VERSION, "entries": {}, "rules": [], "history": []}
        self.load()

    @staticmethod
    def key(text: str, code: str) -> str:
        return f"{text}\t{raw_code(code)}"

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._data.update({key: data.get(key, self._data[key]) for key in self._data})
        except Exception:
            return

    def save(self) -> None:
        write_text_utf8(self.path, json.dumps(self._data, ensure_ascii=False, indent=2))

    def entry(self, text: str, code: str) -> dict:
        return dict(self._data["entries"].get(self.key(text, code), {}))

    def set_entry(self, text: str, code: str, note: str, tags: Iterable[str]) -> None:
        clean_tags = sorted({tag.strip() for tag in tags if tag.strip()})
        key = self.key(text, code)
        if note.strip() or clean_tags:
            self._data["entries"][key] = {"note": note.strip(), "tags": clean_tags}
        else:
            self._data["entries"].pop(key, None)

    def tags_for(self, text: str, code: str) -> tuple[str, ...]:
        return tuple(self.entry(text, code).get("tags", []))

    def rules(self) -> list[dict]:
        return list(self._data["rules"])

    def set_rules(self, rules: list[dict]) -> None:
        self._data["rules"] = rules

    def record(self, action: str, detail: str, entries: Iterable[str] = ()) -> None:
        self._data["history"].append({"at": datetime.now().isoformat(timespec="seconds"), "action": action, "detail": detail, "entries": list(entries)})
        self._data["history"] = self._data["history"][-500:]

    def history(self) -> list[dict]:
        return list(reversed(self._data["history"]))

    def prune(self, phrases: Iterable[Phrase]) -> None:
        valid = {self.key(item.text, item.code) for item in phrases}
        self._data["entries"] = {key: value for key, value in self._data["entries"].items() if key in valid}


def health_check(phrases: Iterable[Phrase], groups=None) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    seen: set[tuple[str, str]] = set()
    for phrase in phrases:
        key = (phrase.text, raw_code(phrase.code))
        if not phrase.text.strip(): issues.append(HealthIssue("empty_text", "存在空文本条目", phrase.text, phrase.code))
        if not raw_code(phrase.code): issues.append(HealthIssue("empty_code", "存在空编码条目", phrase.text, phrase.code))
        if not 1 <= int(phrase.weight) <= 99: issues.append(HealthIssue("weight", "权重不在 1-99 范围", phrase.text, phrase.code))
        if key in seen: issues.append(HealthIssue("exact_duplicate", "存在完全重复条目", phrase.text, phrase.code))
        seen.add(key)
        if groups is not None:
            group = groups.get_entry_group(phrase.text, phrase.code)
            if group and group not in groups.list_groups(): issues.append(HealthIssue("group", f"分组不存在：{group}", phrase.text, phrase.code))
    return issues


def diff_phrase_lines(current: str, backup: str) -> dict[str, list[str]]:
    current_lines = {line for line in current.splitlines() if line and not line.startswith("#")}
    backup_lines = {line for line in backup.splitlines() if line and not line.startswith("#")}
    return {"新增": sorted(current_lines - backup_lines), "删除": sorted(backup_lines - current_lines), "未变": sorted(current_lines & backup_lines)}


def parse_import_text(text: str) -> tuple[list[Phrase], list[str]]:
    entries: list[Phrase] = []
    errors: list[str] = []
    for index, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"): continue
        parts = line.split("\t") if "\t" in line else line.split(",")
        if len(parts) < 2 or not parts[0].strip() or not raw_code(parts[1]):
            errors.append(f"第 {index} 行格式无效"); continue
        try: weight = int(parts[2]) if len(parts) > 2 else 1
        except ValueError: errors.append(f"第 {index} 行权重无效"); continue
        entries.append(Phrase(parts[0].strip(), raw_code(parts[1]), max(1, min(99, weight))))
    return entries, errors


def export_text(phrases: Iterable[Phrase]) -> str:
    return "\n".join(item.to_line() for item in phrases) + "\n"
