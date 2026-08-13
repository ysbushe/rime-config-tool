"""Shared input policy for user-editable Rime codes."""
from __future__ import annotations

import string
import unicodedata

from src.encoding.code_suggestions import normalize_display_code, raw_code


DEFAULT_CODE_ALPHABET = string.ascii_lowercase + string.digits
DISPLAY_SEPARATOR = "'"


def allowed_code_characters(schema_alphabet: str = "") -> frozenset[str]:
    """Return safe keys explicitly allowed by the active Rime speller."""
    source = schema_alphabet or DEFAULT_CODE_ALPHABET
    allowed = {
        char for char in source
        if char.isprintable()
        and not char.isspace()
        and unicodedata.east_asian_width(char) not in {"F", "W"}
    }
    if not allowed:
        allowed.update(DEFAULT_CODE_ALPHABET)
    allowed.add(DISPLAY_SEPARATOR)
    return frozenset(allowed)


def sanitize_code_input(value: str, schema_alphabet: str = "") -> tuple[str, str]:
    """Normalize a code and return ``(accepted, rejected_characters)``."""
    allowed = allowed_code_characters(schema_alphabet)
    accepted: list[str] = []
    # ``normalize_display_code`` intentionally removes spaces around display
    # separators.  Code entry must still report those pasted/typed spaces as
    # rejected instead of silently accepting them.
    rejected: list[str] = [char for char in value if char.isspace()]
    for char in normalize_display_code(value):
        candidate = char
        if char.isascii() and char.isupper() and char.lower() in allowed:
            candidate = char.lower()
        if candidate in allowed:
            accepted.append(candidate)
        else:
            rejected.append(char)
    return "".join(accepted), "".join(dict.fromkeys(rejected))


def validate_code_input(
    value: str,
    schema_alphabet: str = "",
    *,
    allow_empty: bool = False,
) -> str:
    """Return normalized display code or raise a user-facing ``ValueError``."""
    accepted, rejected = sanitize_code_input(value, schema_alphabet)
    if rejected:
        shown = " ".join(repr(char) for char in rejected)
        raise ValueError(f"编码包含当前方案不支持的字符：{shown}")
    if not allow_empty and not raw_code(accepted):
        raise ValueError("编码不能为空。")
    return accepted
