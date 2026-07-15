"""拼音服务（PinyinService）。

封装 pypinyin：
    - get_full_pinyin：无声调全拼（用于「生成全拼」）

pypinyin 不可用时优雅降级（available=False），UI 据此提示。
"""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PinyinService:
    """本地默认全拼能力封装（离线、不依赖云）。"""

    def __init__(self) -> None:
        self._pinyin = None
        self._style = None
        self._syllables: set[str] | None = None
        try:
            from pypinyin import pinyin, Style  # type: ignore

            self._pinyin = pinyin
            self._style = Style
            self.available = True
        except Exception as exc:
            logger.warning("pypinyin 不可用，拼音能力降级：%s", exc)
            self.available = False

    def split_pinyin_code(self, code: str) -> list[str]:
        """Split a continuous pinyin code when every segment is a known syllable."""
        value = re.sub(r"[^a-z]", "", (code or "").lower())
        if not value or not self.available:
            return []
        if self._syllables is None:
            try:
                from pypinyin.constants import PINYIN_DICT  # type: ignore
                syllables: set[str] = set()
                for readings in PINYIN_DICT.values():
                    for reading in readings.split(","):
                        normalized = "".join(
                            char for char in unicodedata.normalize("NFD", reading.lower())
                            if not unicodedata.combining(char) and char.isalpha()
                        )
                        if normalized:
                            syllables.add(normalized)
                self._syllables = syllables
            except Exception:
                self._syllables = set()

        @lru_cache(maxsize=None)
        def split(index: int):
            if index == len(value):
                return ()
            # Prefer long syllables so xiangxing becomes xiang'xing, not xi'a'ng...
            for end in range(min(len(value), index + 6), index, -1):
                part = value[index:end]
                if part not in self._syllables:
                    continue
                tail = split(end)
                if tail is not None:
                    return (part,) + tail
            return None

        result = split(0)
        return list(result) if result and len(result) > 1 else []

    # ------------------------------------------------------------------ #
    def get_full_pinyin(self, text: str) -> str:
        """无声调全拼，拼接为连续字符串（如 '银行' -> 'yinhang'）。"""
        if not self.available or not text:
            return ""
        try:
            result = self._pinyin(
                text, style=self._style.NORMAL, heteronym=False
            )
            # pypinyin 返回 [['yin'], ['hang']]，拼接为连续无声调字符串
            return "".join("".join(chars) for chars in (result or []))
        except Exception as exc:
            logger.warning("生成全拼失败：%s", exc)
            return ""
    def get_pinyin_units(self, text: str) -> list[str]:
        """Return readable units, preserving ASCII runs for mixed phrases."""
        if not text:
            return []
        if self.available:
            try:
                result = self._pinyin(
                    text, style=self._style.NORMAL, heteronym=False
                )
                return [
                    "".join(chars).strip().lower()
                    for chars in (result or [])
                    if "".join(chars).strip()
                ]
            except Exception:
                pass
        units: list[str] = []
        ascii_run = ""

        def flush_ascii() -> None:
            nonlocal ascii_run
            if ascii_run:
                units.append(ascii_run.lower())
                ascii_run = ""

        for char in text.strip():
            if char.isascii() and char.isalnum():
                ascii_run += char
                continue
            flush_ascii()
            if char.isspace():
                continue
            if self.available:
                try:
                    result = self._pinyin(
                        char, style=self._style.NORMAL, heteronym=False
                    )
                    value = "".join(result[0]) if result else ""
                    if value and value != char:
                        units.append(value.lower())
                        continue
                except Exception:
                    pass
            units.append(char.lower())
        flush_ascii()
        return [unit for unit in units if unit]
