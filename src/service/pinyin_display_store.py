"""INI sidecar for user-adjusted display-only pinyin boundaries."""
from __future__ import annotations

import configparser
import hashlib
from pathlib import Path

from src.encoding.code_suggestions import infer_display_code, normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase
from src.service.pinyin_service import PinyinService

DISPLAY_INI_FILENAME = "pinyin_display.ini"


class PinyinDisplayStore:
    def __init__(self, rime_dir: str | Path, pinyin: PinyinService) -> None:
        self.path = Path(rime_dir) / DISPLAY_INI_FILENAME
        self._pinyin = pinyin
        self._values: dict[str, tuple[str, str, str]] = {}
        self._inferred: dict[str, str] = {}
        self.load()

    @staticmethod
    def _id(text: str, code: str) -> str:
        value = f"{text}\t{raw_code(code)}".encode("utf-8")
        return hashlib.sha1(value).hexdigest()

    def load(self) -> None:
        self._values = {}
        self._inferred = {}
        if not self.path.is_file():
            return
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.path, encoding="utf-8")
        for section in parser.sections():
            text = parser.get(section, "text", fallback="")
            code = parser.get(section, "code", fallback="")
            display = parser.get(section, "display", fallback="")
            if text and raw_code(display) == raw_code(code):
                self._values[self._id(text, code)] = (text, raw_code(code), display)

    def display_for(self, phrase: Phrase) -> str:
        key = self._id(phrase.text, phrase.code)
        saved = self._values.get(key)
        if saved:
            return saved[2]
        if key not in self._inferred:
            self._inferred[key] = infer_display_code(phrase.text, phrase.code, self._pinyin)
        return self._inferred[key]

    def set(self, text: str, code: str, display: str) -> None:
        normalized = normalize_display_code(display)
        stored = raw_code(code)
        key = self._id(text, stored)
        self._inferred.pop(key, None)
        if normalized and raw_code(normalized) == stored and normalized != stored:
            self._values[key] = (text, stored, normalized)
        else:
            self._values.pop(key, None)

    def prune(self, phrases: list[Phrase]) -> None:
        valid = {self._id(p.text, p.code) for p in phrases}
        self._values = {key: value for key, value in self._values.items() if key in valid}
        self._inferred = {key: value for key, value in self._inferred.items() if key in valid}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser(interpolation=None)
        for key, (text, code, display) in sorted(self._values.items()):
            parser[f"entry:{key}"] = {"text": text, "code": code, "display": display}
        with self.path.open("w", encoding="utf-8", newline="\n") as handle:
            parser.write(handle)
