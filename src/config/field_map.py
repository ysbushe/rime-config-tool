"""字段映射（FieldMap）。

schema / symbols 两类受管文件「暴露哪些可编辑字段」由 FieldMap 决定，
绝不写死在 UI 中。提供：
    - default_for(kind)：给白霜拼音（frost）的合理默认映射
    - infer_from_yaml(data)：从真实 YAML 自动推断出实际可用字段
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# 默认映射数据源（白霜拼音 frost，已按真实 YAML 校准）。
# 优先从此 JSON 读取，缺失时回退到下方 _FALLBACK 内联默认值。
_DEFAULT_MAP_PATH = Path(__file__).resolve().parent / "default_field_map.json"

# 内联兜底（与 default_field_map.json 保持一致，确保 JSON 缺失时仍可运行）
_FALLBACK: Dict[str, Dict[str, Any]] = {
    "schema": {
        "switch_names": [
            "ascii_mode", "ascii_punct", "traditionalization",
            "full_shape", "search_single_char",
        ],
        "custom_phrase_ref": "table_translator@custom_phrase",
        "custom_phrase_enabled": True,
        "key_binding_labels": [
            "翻页（Page_Up / Page_Down）",
            "选重（Shift+Letter）",
            "光标移动（Left / Right）",
            "编辑（BackSpace / Return）",
        ],
    },
    "symbols": {
        "categories": [
            "/fh", "/dn", "/xq", "/mj", "/sz", "/pk", "/bq", "/tq",
            "/yy", "/lx", "/bg", "/tt", "/xz", "/xh", "/fk", "/jh",
            "/jt", "/sx", "/xl", "/dw", "/hb", "/jg", "/kx", "/bh",
            "/bd", "/py", "/zy", "/sd", "/jm", "/hw",
        ],
    },
}


def _load_default_map() -> Dict[str, Dict[str, Any]]:
    """读取 default_field_map.json；失败则回退内联默认值。"""
    try:
        raw = json.loads(_DEFAULT_MAP_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return _FALLBACK


# 模块级缓存，避免每次构建都读盘
_DEFAULT_MAP_CACHE = _load_default_map()


@dataclass
class FieldMap:
    """描述某一类受管文件的可编辑字段集合。"""

    kind: str  # 'schema' | 'symbols'

    # —— schema 字段 ——
    switch_names: List[str] = field(default_factory=list)
    custom_phrase_ref: str = "table_translator@custom_phrase"
    custom_phrase_enabled: bool = True
    key_binding_labels: List[str] = field(default_factory=list)

    # —— symbols 字段 ——
    categories: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # 默认映射（基于真实 rime_frost.schema.yaml / symbols_v.yaml 校准）
    # ------------------------------------------------------------------ #
    @staticmethod
    def default_for(kind: str) -> "FieldMap":
        """读取 default_field_map.json（或兜底默认值）构建 FieldMap。"""
        spec = _DEFAULT_MAP_CACHE.get(kind)
        if spec is None:
            if kind in ("schema", "symbols"):
                spec = _FALLBACK.get(kind, {})
            else:
                raise ValueError(f"未知 FieldMap 类型：{kind}")
        if kind == "schema":
            return FieldMap(
                kind="schema",
                # 真实 switches 顺序来自 rime_frost.schema.yaml
                switch_names=list(spec.get("switch_names", [])),
                custom_phrase_ref=spec.get(
                    "custom_phrase_ref", "table_translator@custom_phrase"
                ),
                custom_phrase_enabled=bool(spec.get("custom_phrase_enabled", True)),
                # key_bindings 在白霜中 import_preset: default，这里给出常用释义用于展示
                key_binding_labels=list(spec.get("key_binding_labels", [])),
            )
        if kind == "symbols":
            return FieldMap(
                kind="symbols",
                # 从真实 symbols_v.yaml 抽取的常见分类键（/ 模式）
                categories=list(spec.get("categories", [])),
            )
        raise ValueError(f"未知 FieldMap 类型：{kind}")

    # ------------------------------------------------------------------ #
    # 从真实 YAML 推断
    # ------------------------------------------------------------------ #
    def infer_from_yaml(self, data: Dict) -> None:
        """用真实 YAML 数据覆盖默认映射（仅填充「实际存在」的字段）。"""
        if not isinstance(data, dict):
            return
        if self.kind == "schema":
            self._infer_schema(data)
        elif self.kind == "symbols":
            self._infer_symbols(data)

    def _infer_schema(self, data: Dict) -> None:
        switches = data.get("switches") or []
        names: List[str] = []
        for sw in switches:
            if isinstance(sw, dict) and sw.get("name"):
                names.append(str(sw["name"]))
        if names:
            self.switch_names = names

        engine = data.get("engine") or {}
        translators = engine.get("translators") or []
        if translators:
            self.custom_phrase_enabled = self.custom_phrase_ref in translators

    def _infer_symbols(self, data: Dict) -> None:
        symbols = data.get("symbols")
        if isinstance(symbols, dict) and symbols:
            self.categories = list(symbols.keys())
