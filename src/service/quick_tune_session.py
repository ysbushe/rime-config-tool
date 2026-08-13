"""UI-independent search and staged ranking for the quick-tune workbench."""
from __future__ import annotations

from dataclasses import dataclass

from src.encoding.code_suggestions import build_suggestions, contains_han, raw_code
from src.repo.phrase_repo import PhraseRepo
from src.service.pinyin_service import PinyinService


@dataclass(frozen=True)
class TuneCandidate:
    label: str
    text: str
    code: str
    key: str = ""
    source: str = "个人词库"


class QuickTuneSession:
    """Own the detached phrase copy and dictionary-merge rules."""

    def __init__(self, repo: PhraseRepo, pinyin: PinyinService, dictionary_index=None):
        self.repo = repo.clone_in_memory()
        self.pinyin = pinyin
        self.dictionary_index = dictionary_index
        self.dirty = False
        self._refresh_personal_cache()

    def _refresh_personal_cache(self) -> None:
        self._personal_entries = tuple(self.repo.all())
        by_code: dict[str, list] = {}
        for phrase in self._personal_entries:
            by_code.setdefault(raw_code(phrase.code), []).append(phrase)
        self._personal_by_code = by_code

    def search(
        self, keyword: str, *, include_system: bool = True,
    ) -> list[tuple[TuneCandidate, int | None]]:
        rows: list[tuple[TuneCandidate, int | None]] = []
        seen: set[tuple[str, str]] = set()
        for phrase in self._personal_entries:
            if (
                keyword
                and keyword != phrase.text.casefold()
                and raw_code(keyword).casefold() != raw_code(phrase.code).casefold()
            ):
                continue
            rows.append((TuneCandidate(
                phrase.text, phrase.text, phrase.code, phrase.key,
            ), phrase.weight))
            seen.add((phrase.text, phrase.code))
        if not keyword or self.dictionary_index is None or not include_system:
            return rows
        if hasattr(self.dictionary_index, "lookup_exact"):
            system_rows = self.dictionary_index.lookup_exact(keyword, limit=100)
        else:
            system_rows = [
                *self.dictionary_index.lookup_text(keyword, limit=100),
                *self.dictionary_index.lookup_code(raw_code(keyword), limit=100),
                *self.dictionary_index.lookup_generated_code(
                    keyword, self.pinyin.split_pinyin_code, limit=100,
                ),
            ]
        for item in system_rows:
            identity = (item.text, item.code)
            if identity in seen:
                continue
            rows.append((TuneCandidate(
                item.text, item.text, item.code,
                source=candidate_source_label(item.source),
            ), item.weight))
            seen.add(identity)
        if contains_han(keyword) and any(
            candidate.text.casefold() == keyword for candidate, _weight in rows
        ):
            for suggestion in build_suggestions(keyword, self.pinyin):
                code = suggestion.raw_code
                identity = (keyword, code)
                if identity in seen:
                    continue
                rows.append((TuneCandidate(
                    keyword, keyword, code, source="拼音建议",
                ), None))
                seen.add(identity)
        return rows

    def code_candidates(
        self, code: str, limit: int, *, include_system: bool = True,
    ) -> list[TuneCandidate]:
        if not code:
            return []
        items: list[TuneCandidate] = []
        seen: set[tuple[str, str]] = set()
        for phrase in self._personal_by_code.get(raw_code(code), ()):
            items.append(TuneCandidate(
                phrase.text, phrase.text, phrase.code, phrase.key,
            ))
            seen.add((phrase.text, phrase.code))
        index = self.dictionary_index
        if index is not None and include_system:
            candidates = list(index.lookup_code(code, limit=limit * 4))
            if not bool(getattr(index, "handles_shorthand", False)):
                candidates.extend(index.lookup_generated_code(
                    code, self.pinyin.split_pinyin_code, limit=limit * 4,
                ))
            for candidate in candidates:
                identity = (candidate.text, candidate.code)
                if identity in seen:
                    continue
                items.append(TuneCandidate(
                    candidate.text, candidate.text, candidate.code,
                    source=candidate_source_label(candidate.source),
                ))
                seen.add(identity)
        return items[:limit]

    def stage_rank(self, text: str, code: str, action: str) -> tuple[bool, str]:
        phrase = self.repo.find(text, code)
        added = phrase is None
        if added:
            initial_weight = {"top": 99, "up": 2, "down": 1}.get(action)
            if initial_weight is None:
                return False, f"未知排序动作：{action}"
            self.repo.upsert(text, code, initial_weight)
            changed = True
        else:
            try:
                changed = self.repo.adjust_rank(text, code, action)
            except ValueError as exc:
                return False, str(exc)
        if not changed:
            labels = {
                "top": "已经位于暂存顺序首位",
                "up": "在暂存顺序中已经无法继续上移",
                "down": "在暂存顺序中已经无法继续下移",
            }
            return False, labels.get(action, "暂存顺序没有变化。")
        self.dirty = True
        self._refresh_personal_cache()
        labels = {"top": "置顶", "up": "上移", "down": "下移"}
        prefix = "已暂存加入个人词库并" if added else "已暂存"
        return True, prefix + labels.get(action, "排序调整") + "；点击保存后正式更新。"


def candidate_source_label(source: str) -> str:
    labels = {
        "rime_frost": "系统词库",
        "melt_eng": "系统英文词库",
        "wanxiang": "万象词库",
        "wanxiang_english": "万象英文词库",
        "wanxiang_mixedcode": "万象混合编码词库",
    }
    return labels.get(source, source or "系统词库")
