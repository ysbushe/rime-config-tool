"""分组服务（GroupService）。

方案 A：分组作为 sidecar 元数据，不改动 custom_phrase.txt 原生格式。
落地文件：<rime_dir>/custom_phrase.txt.groups.json

结构：
{
  "groups": ["工作", "生活", ...],          # 分组定义（有序）
  "membership": { "文本\\t编码": "分组名" }  # 条目 -> 分组
}
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.config.paths import group_sidecar_path, user_config_dir
from src.utils.encoding import write_text_utf8
from src.utils.logger import get_logger

logger = get_logger(__name__)

_ALL_GROUP = "全部"  # 虚拟分组：表示不过滤

# 分组 sidecar 签名缓存（ini）：文件未变化时跳过重解析，减少重复读取与卡顿
_GROUP_CACHE_INI = user_config_dir() / "groups_cache.ini"


def _grp_sig(path: Path) -> str:
    try:
        st = os.stat(path)
        return f"{int(st.st_mtime)}|{st.st_size}"
    except OSError:
        return ""


def _grp_section(path: Path) -> str:
    return "g_" + hashlib.md5(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


class _GroupCache:
    _cp = configparser.ConfigParser()

    def __init__(self) -> None:
        if _GROUP_CACHE_INI.exists():
            try:
                self._cp.read(_GROUP_CACHE_INI, encoding="utf-8")
            except Exception:
                self._cp = configparser.ConfigParser()

    def cached_sig(self, path: Path) -> Optional[str]:
        sec = _grp_section(path)
        if self._cp.has_section(sec) and self._cp.has_option(sec, "sig"):
            return self._cp.get(sec, "sig")
        return None

    def update(self, path: Path) -> None:
        sig = _grp_sig(path)
        if not sig:
            return
        sec = _grp_section(path)
        if not self._cp.has_section(sec):
            self._cp.add_section(sec)
        self._cp.set(sec, "sig", sig)
        try:
            _GROUP_CACHE_INI.parent.mkdir(parents=True, exist_ok=True)
            with open(_GROUP_CACHE_INI, "w", encoding="utf-8") as fh:
                self._cp.write(fh)
        except Exception:
            pass


@dataclass
class GroupState:
    """分组 sidecar 内存模型。"""

    groups: List[str] = field(default_factory=list)
    membership: Dict[str, str] = field(default_factory=dict)  # entry key -> group


class GroupService:
    """管理词库分组 sidecar。"""

    def __init__(self, rime_dir: str, phrase_filename: str = "custom_phrase.txt") -> None:
        # 未设置 Rime 目录时禁用持久化（不向 CWD 写 sidecar）
        self._enabled = bool(rime_dir)
        self._path = group_sidecar_path(rime_dir, phrase_filename) if self._enabled \
            else Path("custom_phrase.txt.groups.json")
        self._state = GroupState()
        self._cache = _GroupCache()
        self.load()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def load(self) -> None:
        if not self._path.exists():
            self._state = GroupState()
            return
        # ini 签名缓存：文件未变化且已载入过 → 跳过重解析（减少卡顿）
        sig = _grp_sig(self._path)
        if sig and self._cache.cached_sig(self._path) == sig and self._state.groups:
            logger.info("分组 sidecar 未变化，跳过解析（缓存命中）：%s", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._state.groups = list(raw.get("groups", []))
            self._state.membership = dict(raw.get("membership", {}))
            self._cache.update(self._path)
        except Exception as exc:
            logger.warning("读取分组文件失败：%s", exc)
            self._state = GroupState()

    def save(self) -> None:
        if not self._enabled:
            return
        try:
            write_text_utf8(
                self._path,
                json.dumps(
                    {"groups": self._state.groups, "membership": self._state.membership},
                    ensure_ascii=False, indent=2,
                ),
            )
            self._cache.update(self._path)
        except Exception as exc:
            logger.warning("保存分组文件失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 分组定义
    # ------------------------------------------------------------------ #
    def list_groups(self) -> List[str]:
        """返回真实分组名列表（不含虚拟『全部』）。"""
        return list(self._state.groups)

    def add_group(self, name: str, save: bool = True) -> bool:
        name = (name or "").strip()
        if not name or name == _ALL_GROUP:
            return False
        if name not in self._state.groups:
            self._state.groups.append(name)
            if save:
                self.save()
            return True
        return False

    def remove_group(self, name: str) -> bool:
        if name not in self._state.groups:
            return False
        self._state.groups.remove(name)
        # 清理该分组下的成员
        self._state.membership = {
            k: v for k, v in self._state.membership.items() if v != name
        }
        self.save()
        return True

    def rename_group(self, old: str, new: str) -> bool:
        new = (new or "").strip()
        if not new or new == _ALL_GROUP or new in self._state.groups:
            return False
        if old not in self._state.groups:
            return False
        idx = self._state.groups.index(old)
        self._state.groups[idx] = new
        self._state.membership = {
            k: (new if v == old else v) for k, v in self._state.membership.items()
        }
        self.save()
        return True

    # ------------------------------------------------------------------ #
    # 成员关系
    # ------------------------------------------------------------------ #
    def set_entry_group(self, text: str, code: str, group: str, save: bool = True) -> None:
        key = f"{text}\t{code}"
        group = (group or "").strip()
        if not group or group == _ALL_GROUP:
            self._state.membership.pop(key, None)
        else:
            if group not in self._state.groups:
                self.add_group(group, save=save)
            self._state.membership[key] = group
        if save:
            self.save()

    def get_entry_group(self, text: str, code: str) -> str:
        return self._state.membership.get(f"{text}\t{code}", "")

    def remap_entry_key(self, old_text: str, old_code: str,
                        new_text: str, new_code: str) -> None:
        """词条文本/编码变更后，迁移其分组归属（旧 key → 新 key）。"""
        old_key = f"{old_text}\t{old_code}"
        new_key = f"{new_text}\t{new_code}"
        if old_key == new_key:
            return
        grp = self._state.membership.pop(old_key, None)
        if grp is None:
            return
        # 新 key 已存在归属则保留原值，否则写入迁移后的分组
        self._state.membership.setdefault(new_key, grp)
        self.save()

    def entries_of(self, group: str) -> List[str]:
        """返回某分组的 entry key 列表。"""
        return [k for k, v in self._state.membership.items() if v == group]

    @staticmethod
    def all_group_label() -> str:
        return _ALL_GROUP
