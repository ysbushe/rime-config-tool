"""UTF-8 helpers used by Rime-managed text and configuration files."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import List, Union

UTF8_BOM = b"\xef\xbb\xbf"


def read_text_utf8(path: Union[str, Path]) -> str:
    """Read UTF-8 text and remove an optional BOM."""
    raw = Path(path).read_bytes()
    if raw.startswith(UTF8_BOM):
        raw = raw[len(UTF8_BOM):]
    return raw.decode("utf-8")


def write_text_utf8(path: Union[str, Path], text: str) -> None:
    """Atomically replace a file with UTF-8 text without a BOM."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_lines_utf8(path: Union[str, Path]) -> List[str]:
    return read_text_utf8(path).splitlines()


def has_bom(path: Union[str, Path]) -> bool:
    return Path(path).read_bytes().startswith(UTF8_BOM)
