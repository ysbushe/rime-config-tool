"""编码工具：严格遵守「UTF-8 无 BOM」约定。

RIME 文本/配置文件的铁律：
    - 读：去除 UTF-8 BOM 后返回字符串
    - 写：以 UTF-8 无 BOM 回写
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Union

UTF8_BOM = b"\xef\xbb\xbf"


def read_text_utf8(path: Union[str, Path]) -> str:
    """读取文本文件，自动去除 UTF-8 BOM，返回 str。"""
    raw = Path(path).read_bytes()
    if raw.startswith(UTF8_BOM):
        raw = raw[len(UTF8_BOM):]
    return raw.decode("utf-8")


def write_text_utf8(path: Union[str, Path], text: str) -> None:
    """以 UTF-8 无 BOM 写回文本文件。"""
    Path(path).write_bytes(text.encode("utf-8"))


def read_lines_utf8(path: Union[str, Path]) -> List[str]:
    """按行读取（保留原始换行拆分，去 BOM），返回行列表。"""
    return read_text_utf8(path).splitlines()


def has_bom(path: Union[str, Path]) -> bool:
    """判断文件是否带 UTF-8 BOM。"""
    data = Path(path).read_bytes()
    return data.startswith(UTF8_BOM)
