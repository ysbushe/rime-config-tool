"""磁盘缓存（ini）。

记录受管文件的 mtime+size 签名，当文件『未变化』时供仓储跳过重复解析，
从而减少切换选项卡时的卡顿感（与 PhraseRepo / SchemaRepo / SymbolsRepo 配合）。

缓存文件：用户配置目录下的 config/cache.ini（不触碰任何 Rime 配置）。
"""
from __future__ import annotations

import configparser
import hashlib
import os
from pathlib import Path
from typing import Optional

from src.config.paths import user_config_dir

_SECTION_PREFIX = "file_"


class CacheService:
    """基于 ini 的文件签名缓存。"""

    def __init__(self, namespace: str = "rimeconfig") -> None:
        self._file = user_config_dir() / "cache.ini"
        self._cp = configparser.ConfigParser()
        if self._file.exists():
            try:
                self._cp.read(self._file, encoding="utf-8")
            except Exception:
                self._cp = configparser.ConfigParser()

    # ------------------------------------------------------------------ #
    @staticmethod
    def signature_of(path) -> str:
        """返回文件当前签名（mtime|size）；文件不存在返回空串。"""
        try:
            st = os.stat(path)
            return f"{int(st.st_mtime)}|{st.st_size}"
        except OSError:
            return ""

    def _section(self, path) -> str:
        key = str(Path(path).resolve())
        return _SECTION_PREFIX + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]

    def cached_signature(self, path) -> Optional[str]:
        sec = self._section(path)
        if self._cp.has_section(sec) and self._cp.has_option(sec, "sig"):
            return self._cp.get(sec, "sig")
        return None

    def is_fresh(self, path) -> bool:
        """文件签名是否与上次缓存一致（一致则无需重新解析）。"""
        return self.cached_signature(path) == self.signature_of(path)

    def update(self, path) -> None:
        """记录文件当前签名到 ini。"""
        sig = self.signature_of(path)
        if not sig:
            return
        sec = self._section(path)
        if not self._cp.has_section(sec):
            self._cp.add_section(sec)
        self._cp.set(sec, "sig", sig)
        self._write()

    def _write(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as fh:
                self._cp.write(fh)
        except Exception:
            # 缓存写入失败不应影响主流程
            pass
