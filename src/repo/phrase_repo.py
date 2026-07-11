"""词库仓储（PhraseRepo）。

负责 custom_phrase.txt 的读写与内存模型维护，严格遵循：
    - 行格式：文本<Tab>编码<Tab>权重（Tab 分隔，禁用空格/逗号分隔）
    - 编码为任意字符串（允许简码 / 缩写 / 英文 id）
    - 权重为整数，越大越靠前；缺省默认 1
    - 同 text+code 冲突：更新为用户确认的权重并标记，不新增重复行
    - 头注释行（以 # 开头）保留，数据区不写注释
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from src.utils.encoding import read_text_utf8, write_text_utf8
from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WEIGHT = 1


@dataclass
class Phrase:
    """单条自定义短语。"""

    text: str
    code: str
    weight: int = DEFAULT_WEIGHT
    is_conflict: bool = False  # 仅 UI 高亮用的瞬时标记

    @property
    def key(self) -> str:
        """条目唯一标识（text + code）。"""
        return f"{self.text}\t{self.code}"

    def to_line(self) -> str:
        """序列化为 RIME 行。"""
        return f"{self.text}\t{self.code}\t{self.weight}"


class PhraseRepo:
    """custom_phrase.txt 仓储。"""

    def __init__(self, path: str, cache: object = None) -> None:
        self.path = Path(path)
        self._header: List[str] = []
        self._entries: List[Phrase] = []
        self._cache = cache  # 可选：CacheService，用于跳过未变化文件的重复解析
        self.load()

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def load(self, force: bool = False) -> None:
        # 命中缓存且已有数据 → 跳过磁盘重解析（减少切换卡顿）
        if not force and self._cache is not None and self._entries \
                and self._cache.is_fresh(self.path):
            logger.info("词库未变化，跳过解析（缓存命中）：%s", self.path)
            return
        self._entries = []
        self._header = []
        # 路径为空或为目录（如未设置 Rime 目录）→ 安全降级为空仓储
        if not self.path.is_file():
            logger.info("词库文件不存在或路径无效，初始化空仓储：%s", self.path)
            return
        content = read_text_utf8(self.path)
        for raw_line in content.splitlines():
            if raw_line.startswith("#"):
                self._header.append(raw_line)
                continue
            if raw_line.strip() == "":
                continue
            parts = raw_line.split("\t")
            text = parts[0]
            code = parts[1] if len(parts) > 1 else ""
            weight = self._parse_weight(parts[2] if len(parts) > 2 else "")
            self._entries.append(Phrase(text=text, code=code, weight=weight))
        logger.info("已载入词库 %d 条：%s", len(self._entries), self.path)

    @staticmethod
    def _parse_weight(raw: str) -> int:
        raw = (raw or "").strip()
        if raw.isdigit():
            return int(raw)
        return DEFAULT_WEIGHT

    # ------------------------------------------------------------------ #
    # 写出（严格 Tab 铁律）
    # ------------------------------------------------------------------ #
    def save(self) -> None:
        # 路径为空或为目录时不写入，避免误写当前目录 / 磁盘根
        if not str(self.path) or self.path.is_dir():
            logger.warning("词库路径无效，跳过写入：%s", self.path)
            return
        lines = list(self._header)
        for entry in self._entries:
            lines.append(entry.to_line())
        write_text_utf8(self.path, "\n".join(lines) + "\n")
        if self._cache is not None:
            self._cache.update(self.path)
        logger.info("已写回词库 %d 条：%s", len(self._entries), self.path)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def all(self) -> List[Phrase]:
        return list(self._entries)

    def count(self) -> int:
        return len(self._entries)

    def find(self, text: str, code: str) -> Optional[Phrase]:
        for e in self._entries:
            if e.text == text and e.code == code:
                return e
        return None

    def search(self, keyword: str) -> List[Phrase]:
        kw = (keyword or "").strip().lower()
        if not kw:
            return self.all()
        return [
            e for e in self._entries
            if kw in e.text.lower() or kw in e.code.lower()
        ]

    def sort_by(self, key: str, reverse: bool = False) -> List[Phrase]:
        """返回排序后的副本。key ∈ {weight, code, text, order}。

        order = 文件原始行序（即加入顺序）；reverse=True 时最新加入在前。
        """
        if key == "weight":
            return sorted(self._entries, key=lambda e: e.weight, reverse=reverse)
        if key == "code":
            return sorted(self._entries, key=lambda e: e.code)
        if key == "text":
            return sorted(self._entries, key=lambda e: e.text)
        if key == "order":
            return list(self._entries)[::-1] if reverse else list(self._entries)
        return self.all()

    # ------------------------------------------------------------------ #
    # 变更
    # ------------------------------------------------------------------ #
    def upsert(self, text: str, code: str, weight: Optional[int] = None) -> Tuple[Phrase, bool, bool]:
        """新增或更新。

        返回 (phrase, is_new, conflict)：
            - 若不存在 → 新增，is_new=True, conflict=False
            - 若已存在同 text+code → 使用传入权重并标记 conflict，is_new=False
        """
        existing = self.find(text, code)
        if existing is not None:
            if weight is not None:
                existing.weight = max(1, min(99, int(weight)))
            existing.is_conflict = True
            logger.info("冲突：%s 已存在，保留单条并更新权重 → %d",
                        existing.key, existing.weight)
            return existing, False, True
        w = max(1, min(99, int(weight if weight is not None else DEFAULT_WEIGHT)))
        phrase = Phrase(text=text, code=code, weight=w)
        self._entries.append(phrase)
        return phrase, True, False

    def update_weight(self, text: str, code: str, weight: int) -> bool:
        e = self.find(text, code)
        if e:
            e.weight = weight
            return True
        return False

    def delete(self, text: str, code: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.text == text and e.code == code:
                self._entries.pop(i)
                return True
        return False

    def replace(self, old_text: str, old_code: str, new_text: str,
                new_code: str, new_weight: int) -> Tuple[Phrase, bool, bool]:
        """编辑：先删旧、再以新值 upsert（复用冲突语义）。"""
        self.delete(old_text, old_code)
        return self.upsert(new_text, new_code, new_weight)
