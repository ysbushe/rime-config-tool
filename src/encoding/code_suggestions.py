"""Encoding display helpers and short-code suggestion generation."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from src.service.pinyin_service import PinyinService


DISPLAY_SEPARATOR = "'"
_SEPARATOR_ALIASES = str.maketrans({
    "’": DISPLAY_SEPARATOR,
    "‘": DISPLAY_SEPARATOR,
    "`": DISPLAY_SEPARATOR,
    "｀": DISPLAY_SEPARATOR,
    "＇": DISPLAY_SEPARATOR,
})


@dataclass(frozen=True)
class EncodingSuggestion:
    label: str
    display_code: str

    @property
    def raw_code(self) -> str:
        return raw_code(self.display_code)


def normalize_display_code(code: str) -> str:
    """Normalize easy-to-type separator aliases without restricting custom codes."""
    value = (code or "").translate(_SEPARATOR_ALIASES)
    value = re.sub(r"\s*'\s*", DISPLAY_SEPARATOR, value)
    value = re.sub(r"'{2,}", DISPLAY_SEPARATOR, value)
    return value.strip(DISPLAY_SEPARATOR + " ")


def raw_code(code: str) -> str:
    """Return the value written to Rime; apostrophes are display metadata only."""
    return normalize_display_code(code).replace(DISPLAY_SEPARATOR, "")


def build_suggestions(text: str, pinyin: PinyinService) -> list[EncodingSuggestion]:
    english = _english_suggestions(text)
    if english:
        return english
    units = pinyin.get_pinyin_units(text)
    if not units:
        return []
    values = [EncodingSuggestion("全拼", DISPLAY_SEPARATOR.join(units))]
    if len(units) > 1:
        values.extend([
            EncodingSuggestion("严格简拼", DISPLAY_SEPARATOR.join(u[:1] for u in units if u)),
            EncodingSuggestion("紧凑简拼", DISPLAY_SEPARATOR.join(
                u if i == 0 else u[:1] for i, u in enumerate(units))),
            EncodingSuggestion("混剪简拼", DISPLAY_SEPARATOR.join(
                u[:1] if i == 0 else u for i, u in enumerate(units))),
        ])
    unique: list[EncodingSuggestion] = []
    seen: set[str] = set()
    for suggestion in values:
        if suggestion.display_code and suggestion.display_code not in seen:
            seen.add(suggestion.display_code)
            unique.append(suggestion)
    return unique


def infer_display_code(text: str, stored_code: str, pinyin: PinyinService) -> str:
    """Infer readable syllable boundaries for old entries without changing files."""
    code = raw_code(stored_code)
    if not code:
        return ""
    units = pinyin.get_pinyin_units(text)
    if len(units) < 2:
        return code
    if code == "".join(units):
        return DISPLAY_SEPARATOR.join(units)

    @lru_cache(maxsize=None)
    def split(unit_index: int, code_index: int):
        if unit_index == len(units):
            return (0, ()) if code_index == len(code) else None
        remaining_units = len(units) - unit_index - 1
        max_len = len(code) - code_index - remaining_units
        best = None
        unit = units[unit_index].lower()
        for length in range(1, max_len + 1):
            part = code[code_index:code_index + length]
            if not unit.startswith(part.lower()):
                continue
            tail = split(unit_index + 1, code_index + length)
            if tail is None:
                continue
            score = length * length + tail[0]
            candidate = (score, (part,) + tail[1])
            if best is None or candidate[0] > best[0]:
                best = candidate
        return best

    matched = split(0, 0)
    return DISPLAY_SEPARATOR.join(matched[1]) if matched else code


def _english_suggestions(text: str) -> list[EncodingSuggestion]:
    """Offer predictable choices for pure English words and phrases."""
    value = (text or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z][A-Za-z -]*", value):
        return []
    compact = re.sub(r"[ -]+", "", value)
    options = [
        EncodingSuggestion("英文小写", compact.lower()),
        EncodingSuggestion("英文大写", compact.upper()),
        EncodingSuggestion("原样保留", compact),
    ]
    unique: list[EncodingSuggestion] = []
    seen: set[str] = set()
    for option in options:
        if option.display_code not in seen:
            seen.add(option.display_code)
            unique.append(option)
    return unique