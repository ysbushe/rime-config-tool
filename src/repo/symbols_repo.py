"""符号表仓储（SymbolsRepo）。

管理 symbols_v.yaml，遵循：
    - 暴露字段由 FieldMap 决定（分类键来自 symbols: 字典）
    - 分类 + 符号条目 CRUD
    - 先 deepcopy 再改，写前由 BackupService 备份
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.config.field_map import FieldMap
from src.utils.encoding import read_text_utf8, write_text_utf8
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SymbolsRepo:
    """symbols_v.yaml 仓储。"""

    def __init__(self, path: str, field_map: Optional[FieldMap] = None,
                 cache: object = None) -> None:
        self.path = Path(path)
        self.field_map = field_map or FieldMap.default_for("symbols")
        self._data: Dict[str, Any] = {"symbols": {}}
        self._load_error = ""
        self._cache = cache  # 可选：CacheService，用于跳过未变化文件的重复解析
        self.load()

    # ------------------------------------------------------------------ #
    # 读取 / 写出
    # ------------------------------------------------------------------ #
    def load(self, force: bool = False) -> None:
        self._load_error = ""
        # 命中缓存且已有数据 → 跳过磁盘重解析（减少切换卡顿）
        if not force and self._cache is not None and self._data.get("symbols") \
                and self._cache.is_fresh(self.path):
            logger.info("符号表未变化，跳过解析（缓存命中）：%s", self.path)
            return
        # 路径为空或为目录（如未设置 Rime 目录）→ 安全降级为空数据
        if not self.path.is_file():
            logger.warning("符号表文件不存在或路径无效：%s", self.path)
            self._data = {"symbols": {}}
            return
        text = read_text_utf8(self.path)
        # 同 SchemaRepo：Rime YAML 可能含 TAB，归一化后解析；失败时禁止保存覆盖原文件。
        try:
            data = yaml.safe_load(text.replace("\t", " "))
        except yaml.YAMLError as exc:
            self._load_error = f"符号表解析失败：{exc}"
            logger.warning("%s：%s", self._load_error, self.path)
            self._data = {"symbols": {}}
            self.field_map.infer_from_yaml(self._data)
            return
        if data is not None and not isinstance(data, dict):
            self._load_error = "符号表顶层结构不是 YAML 映射"
            logger.warning("%s：%s", self._load_error, self.path)
            self._data = {"symbols": {}}
            self.field_map.infer_from_yaml(self._data)
            return
        self._data = copy.deepcopy(data or {"symbols": {}})
        if "symbols" not in self._data or not isinstance(self._data["symbols"], dict):
            self._data["symbols"] = {}
        self.field_map.infer_from_yaml(self._data)

    def save(self) -> None:
        if self._load_error:
            raise RuntimeError(f"{self._load_error}，已阻止写回。请先修复 YAML 或从备份恢复。")
        # 路径无效时不写入，避免误写当前目录
        if not str(self.path) or self.path.is_dir():
            logger.warning("符号表路径无效，跳过写入：%s", self.path)
            return
        out = yaml.safe_dump(
            self._data, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=4096,
        )
        write_text_utf8(self.path, out)
        if self._cache is not None:
            self._cache.update(self.path)
        logger.info("已写回符号表：%s", self.path)

    @property
    def load_error(self) -> str:
        """非空表示文件未能安全解析，界面应禁止保存。"""
        return self._load_error

    # ------------------------------------------------------------------ #
    # 分类
    # ------------------------------------------------------------------ #
    def categories(self) -> List[str]:
        return list((self._data.get("symbols") or {}).keys())

    def category_exists(self, category: str) -> bool:
        return category in (self._data.get("symbols") or {})

    def add_category(self, category: str) -> None:
        if not category:
            return
        symbols = self._data.setdefault("symbols", {})
        if category not in symbols:
            symbols[category] = []

    def remove_category(self, category: str) -> None:
        symbols = self._data.get("symbols") or {}
        symbols.pop(category, None)

    # ------------------------------------------------------------------ #
    # 符号条目
    # ------------------------------------------------------------------ #
    def get_symbols(self, category: str) -> List[str]:
        raw = (self._data.get("symbols") or {}).get(category, [])
        # 兼容「单字符未用列表包裹」的情况
        if isinstance(raw, str):
            return list(raw)
        return list(raw)

    def set_symbols(self, category: str, symbols: List[str]) -> None:
        self._data.setdefault("symbols", {})[category] = list(symbols)

    def add_symbol(self, category: str, symbol: str) -> bool:
        if not symbol:
            return False
        self.add_category(category)
        symbols = self.get_symbols(category)
        if symbol not in symbols:
            symbols.append(symbol)
            self.set_symbols(category, symbols)
            return True
        return False

    def remove_symbol(self, category: str, symbol: str) -> bool:
        symbols = self.get_symbols(category)
        if symbol in symbols:
            symbols.remove(symbol)
            self.set_symbols(category, symbols)
            return True
        return False