"""方案仓储（SchemaRepo）。

管理 rime_frost.schema.yaml，遵循：
    - 先 deepcopy 再改，写前由 BackupService 备份
    - MVP 允许重写丢注释（safe_dump 重写）
    - 暴露字段由 FieldMap 决定（不写死）
    - 受限配置：switches 复选 / 启用 custom_phrase 开关 / key_bindings 展示
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


class SchemaRepo:
    """rime_frost.schema.yaml 仓储。"""

    def __init__(self, path: str, field_map: Optional[FieldMap] = None,
                 cache: object = None) -> None:
        self.path = Path(path)
        self.field_map = field_map or FieldMap.default_for("schema")
        self._data: Dict[str, Any] = {}
        self._switch_defs: Dict[str, Any] = {}  # 已移除开关的缓存定义（便于恢复）
        self._load_error = ""
        self._cache = cache  # 可选：CacheService，用于跳过未变化文件的重复解析
        self.load()

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def load(self, force: bool = False) -> None:
        self._load_error = ""
        self._switch_defs = {}
        # 命中缓存且已有数据 → 跳过磁盘重解析（减少切换卡顿）
        if not force and self._cache is not None and self._data \
                and self._cache.is_fresh(self.path):
            logger.info("方案未变化，跳过解析（缓存命中）：%s", self.path)
            return
        # 路径为空或为目录（如未设置 Rime 目录）→ 安全降级为空数据
        if not self.path.is_file():
            logger.warning("方案文件不存在或路径无效：%s", self.path)
            self._data = {}
            return
        text = read_text_utf8(self.path)
        # Rime 的 YAML 常含 TAB 分隔（pyyaml 严格解析会抛 ScannerError）。
        # 归一化为空格后再解析；仍失败时进入只读错误态，禁止后续保存覆盖原文件。
        try:
            data = yaml.safe_load(text.replace("\t", " "))
        except yaml.YAMLError as exc:
            self._load_error = f"方案文件解析失败：{exc}"
            logger.warning("%s：%s", self._load_error, self.path)
            self._data = {}
            self.field_map.infer_from_yaml(self._data)
            return
        if data is not None and not isinstance(data, dict):
            self._load_error = "方案文件顶层结构不是 YAML 映射"
            logger.warning("%s：%s", self._load_error, self.path)
            self._data = {}
            self.field_map.infer_from_yaml(self._data)
            return
        self._data = copy.deepcopy(data or {})
        self.field_map.infer_from_yaml(self._data)
        # 缓存原始开关定义，供「取消勾选后再勾选」恢复
        for sw in (self._data.get("switches") or []):
            if isinstance(sw, dict) and sw.get("name"):
                self._switch_defs[sw["name"]] = copy.deepcopy(sw)

    def save(self) -> None:
        if self._load_error:
            raise RuntimeError(f"{self._load_error}，已阻止写回。请先修复 YAML 或从备份恢复。")
        # 路径无效时不写入，避免误写当前目录
        if not str(self.path) or self.path.is_dir():
            logger.warning("方案路径无效，跳过写入：%s", self.path)
            return
        out = yaml.safe_dump(
            self._data, allow_unicode=True, sort_keys=False,
            default_flow_style=False, width=4096,
        )
        write_text_utf8(self.path, out)
        if self._cache is not None:
            self._cache.update(self.path)
        logger.info("已写回方案：%s", self.path)

    @property
    def load_error(self) -> str:
        """非空表示文件未能安全解析，界面应禁止保存。"""
        return self._load_error

    # ------------------------------------------------------------------ #
    # 只读元信息
    # ------------------------------------------------------------------ #
    def schema_id(self) -> str:
        return str((self._data.get("schema") or {}).get("schema_id", ""))

    def schema_name(self) -> str:
        return str((self._data.get("schema") or {}).get("name", ""))

    def schema_version(self) -> str:
        return str((self._data.get("schema") or {}).get("version", ""))

    # ------------------------------------------------------------------ #
    # switches 复选
    # ------------------------------------------------------------------ #
    def get_switches(self) -> List[Dict[str, Any]]:
        return list(self._data.get("switches") or [])

    def switch_enabled(self, name: str) -> bool:
        return any(
            isinstance(sw, dict) and sw.get("name") == name
            for sw in (self._data.get("switches") or [])
        )

    def set_switch_enabled(self, name: str, enabled: bool) -> None:
        """勾选 = 开关存在于 switches 列表；取消 = 从列表移除（缓存定义可恢复）。"""
        switches = self._data.setdefault("switches", [])
        present = any(isinstance(s, dict) and s.get("name") == name for s in switches)
        if enabled and not present:
            # 恢复缓存定义，缺失则用最小定义
            definition = self._switch_defs.get(name) or {"name": name}
            switches.append(copy.deepcopy(definition))
            if name not in self._switch_defs:
                self._switch_defs[name] = copy.deepcopy(definition)
        elif not enabled and present:
            self._data["switches"] = [
                s for s in switches
                if not (isinstance(s, dict) and s.get("name") == name)
            ]

    # ------------------------------------------------------------------ #
    # 启用 custom_phrase 开关
    # ------------------------------------------------------------------ #
    def custom_phrase_enabled(self) -> bool:
        translators = (self._data.get("engine") or {}).get("translators") or []
        return self.field_map.custom_phrase_ref in translators

    def set_custom_phrase_enabled(self, enabled: bool) -> None:
        engine = self._data.setdefault("engine", {})
        translators = engine.setdefault("translators", [])
        ref = self.field_map.custom_phrase_ref
        has = ref in translators
        if enabled and not has:
            translators.append(ref)
        elif not enabled and has:
            engine["translators"] = [t for t in translators if t != ref]

    # ------------------------------------------------------------------ #
    # key_bindings 展示
    # ------------------------------------------------------------------ #
    def key_bindings_labels(self) -> List[str]:
        return list(self.field_map.key_binding_labels)