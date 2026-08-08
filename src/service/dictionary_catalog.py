"""Discover dictionaries used by the currently selected Rime schema."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import unicodedata

import yaml

from src.encoding.code_suggestions import raw_code
from src.service.rime_profiles import (
    active_schema_ids,
    last_selected_schema_id,
    scheme_dictionary_roots,
    scheme_title,
)


@dataclass(frozen=True)
class DictionaryCatalogItem:
    scheme_id: str
    title: str
    relative_path: str
    purpose: str
    kind: str
    path: Path
    entry_paths: tuple[Path, ...] = ()
    editable: bool = False

    @property
    def is_container(self) -> bool:
        return len(self.entry_paths) > 1


@dataclass(frozen=True)
class DictionaryEntry:
    text: str
    code: str
    weight: str
    source_file: str


_PURPOSES = {
    "rime_frost.dict.yaml": "白霜中文主词典挂载入口",
    "melt_eng.dict.yaml": "白霜英文输入词典入口",
    "radical_pinyin.dict.yaml": "部件拆字与反查词典",
    "cn_dicts/8105.dict.yaml": "常用规范汉字表",
    "cn_dicts/41448.dict.yaml": "Unihan 大字表（通常按需启用）",
    "cn_dicts/base.dict.yaml": "白霜基础词库",
    "cn_dicts/ext.dict.yaml": "含多音字注音的扩展词库",
    "cn_dicts/tencent.dict.yaml": "大型扩展词库",
    "cn_dicts/others.dict.yaml": "白霜杂项词库",
    "en_dicts/en.dict.yaml": "常见英文单词词库",
    "en_dicts/en_ext.dict.yaml": "缩写及互联网相关英文扩展词库",
    "wanxiang.dict.yaml": "万象中文主词典挂载入口",
    "wanxiang_english.dict.yaml": "万象英文方案词典入口",
    "wanxiang_mixedcode.dict.yaml": "万象中英混合编码词典入口",
    "wanxiang_reverse.dict.yaml": "万象生僻字、拆字与聚合反查词典",
    "dicts/zi.dict.yaml": "单字及带调辅助码映射词库",
    "dicts/jichu.dict.yaml": "2～4 字基础高频词库",
    "dicts/lianxiang.dict.yaml": "5 字以上联想长词库",
    "dicts/duoyin.dict.yaml": "多音字兼容词组",
    "dicts/cuoyin.dict.yaml": "错音错字兼容与提示词库",
    "dicts/shici.dict.yaml": "古诗词与文言文词库",
    "dicts/fangyan.dict.yaml": "方言词汇",
    "dicts/wuzhong.dict.yaml": "动植物名称",
    "dicts/diming.dict.yaml": "国内地名",
    "dicts/taifeng.dict.yaml": "台风名称",
    "dicts/huaxue.dict.yaml": "化学词汇",
    "dicts/yaopin.dict.yaml": "药品词汇",
    "dicts/yixue.dict.yaml": "医学词汇",
    "dicts/yiren.dict.yaml": "艺人姓名",
    "dicts/mingren.dict.yaml": "名人姓名",
    "dicts/renming.dict.yaml": "普通高频人名",
    "dicts/mixed.dict.yaml": "中英混合词汇",
    "dicts/en.dict.yaml": "带词频的基础英文词库",
    "custom_phrase.txt": "个人快捷短语",
    "wanxiang_user_phrase.txt": "本工具维护的万象个人短语",
}

_CELL_PURPOSES = {
    "animal": "动物词汇", "chess": "棋类词汇",
    "chess2": "棋类扩展词汇", "computer": "计算机词汇",
    "composite": "综合扩展词汇", "exthot": "热门扩展词汇",
    "food": "饮食词汇", "game": "游戏词汇",
    "geography": "地理词汇", "history": "历史词汇",
    "idiom": "成语词汇", "industry_product": "工业产品词汇",
    "inputmethod": "输入法相关词汇", "literature": "文学词汇",
    "medication": "药物词汇", "media": "媒体词汇",
    "music": "音乐词汇", "name": "人名词汇",
    "name2": "人名扩展词汇", "place": "地点词汇",
    "shulihua": "数理化词汇", "sport": "体育词汇",
}

_EXTENSION_PATTERNS = {
    "rime_frost": {
        "english": (
            "melt_eng.dict.yaml", "melt_eng.schema.yaml",
            "en_dicts/en*.dict.yaml",
        ),
        "mixed": ("en_dicts/cn_en*",),
        "display": ("pinyin_display.ini",),
    },
    "wanxiang": {
        "english": ("wanxiang_english.dict.yaml", "dicts/en.dict.yaml"),
        "mixed": ("wanxiang_mixedcode.dict.yaml", "dicts/mixed.dict.yaml"),
        "display": ("pinyin_display.ini",),
    },
}


def input_code(code: str) -> str:
    """Return the plain code that users can type and personal overrides store."""
    value = (code or "").casefold().translate(str.maketrans({
        "ü": "v", "ǖ": "v", "ǘ": "v", "ǚ": "v", "ǜ": "v",
    }))
    value = "".join(
        character for character in unicodedata.normalize("NFD", value)
        if not unicodedata.combining(character)
    )
    return raw_code(value).replace(" ", "")


def dictionary_catalog_signature(rime_dir: str | Path | None) -> tuple:
    """Return a cheap invalidation key for the settings-page catalog summary."""
    if not rime_dir:
        return ("",)
    root = Path(rime_dir)
    if not root.is_dir():
        return (str(root),)
    watched = []
    for path in (
        root / "default.custom.yaml", root / "default.yaml",
        root / "build" / "default.yaml", *sorted(root.glob("*.dict.yaml")),
    ):
        try:
            stat = path.stat()
        except OSError:
            continue
        watched.append((path.name, stat.st_size, stat.st_mtime_ns))
    return (str(root.resolve()), *watched)


def dictionary_catalog_snapshot(rime_dir: str | Path | None) -> dict:
    """Build the scheme-scoped counts and extension-file summary once."""
    service = DictionaryCatalogService(rime_dir or "")
    schemes = service.available_schemes()
    catalogs = {}
    for schema_id in schemes:
        items = service.catalog(schema_id)
        catalogs[schema_id] = {
            "total": len(items),
            "extensions": sum(
                item.kind != "系统词典入口" for item in items
            ),
        }
    specs = _EXTENSION_PATTERNS.get(
        service.preferred_scheme(),
        {"english": (), "mixed": (), "display": ("pinyin_display.ini",)},
    )
    root = service.root
    extensions = {}
    for key, patterns in specs.items():
        paths = []
        if rime_dir:
            for pattern in patterns:
                paths.extend(path for path in root.glob(pattern) if path.is_file())
        extensions[key] = [str(path) for path in sorted(set(paths))]
    return {
        "schemes": list(schemes),
        "catalogs": catalogs,
        "extensions": extensions,
    }


class DictionaryCatalogService:
    """Build a scheme-scoped catalog without mixing installed schemas."""

    def __init__(self, rime_dir: str | Path) -> None:
        self._available = bool(str(rime_dir))
        self.root = Path(rime_dir) if self._available else Path()

    def available_schemes(self) -> tuple[str, ...]:
        if not self._available or not self.root.is_dir():
            return ()
        active = active_schema_ids(self.root)
        if active:
            return active
        return ()

    def preferred_scheme(self) -> str:
        schemes = self.available_schemes()
        previous = last_selected_schema_id(self.root)
        return previous if previous in schemes else (schemes[0] if schemes else "")

    def catalog(self, schema_id: str) -> list[DictionaryCatalogItem]:
        if not self._available or not schema_id or not self.root.is_dir():
            return []
        known_roots = tuple(name for name, _quality in scheme_dictionary_roots(schema_id))
        root_names = list(known_roots)
        for name in self._schema_dictionary_names(schema_id):
            if name not in root_names:
                root_names.append(name)

        items: list[DictionaryCatalogItem] = []
        seen: set[Path] = set()
        for name in root_names:
            root_path = self._dictionary_path(name)
            if not root_path.is_file():
                continue
            expanded = tuple(self._expand_table(root_path))
            items.append(self._item(
                schema_id, root_path, "系统词典入口", expanded or (root_path,)
            ))
            seen.add(root_path.resolve())
            for path in expanded:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                items.append(self._item(schema_id, path, "系统词典", (path,)))

        for path in self._personal_phrase_paths(schema_id):
            if path.is_file() and path.resolve() not in seen:
                seen.add(path.resolve())
                items.append(self._item(
                    schema_id, path, "个人文本词典", (path,), editable=True
                ))

        for name in root_names[:1] or (schema_id,):
            userdb = self.root / f"{name}.userdb"
            if userdb.is_dir():
                items.append(DictionaryCatalogItem(
                    schema_id, f"{name} 学习词库", userdb.name,
                    "Rime 自动学习产生的用户词典；为避免锁冲突，仅显示状态",
                    "学习词库（只读）", userdb, (), False,
                ))
        return items

    def read_entries(
            self, item: DictionaryCatalogItem, query: str = "",
            limit: int = 500,
    ) -> list[DictionaryEntry]:
        """Read a bounded view of text dictionaries without touching userdb."""
        needle = query.strip().casefold()
        result: list[DictionaryEntry] = []
        for path in item.entry_paths:
            for entry in self._read_entry_file(path):
                if needle and needle not in {
                    entry.text.casefold(), input_code(entry.code).casefold(),
                }:
                    continue
                result.append(entry)
                if len(result) >= limit:
                    return result
        return result

    def _read_entry_file(self, path: Path) -> Iterable[DictionaryEntry]:
        try:
            stream = path.open("r", encoding="utf-8-sig", errors="ignore")
        except OSError:
            return
        in_body = path.suffix.casefold() == ".txt"
        relative = self._relative(path)
        with stream:
            for raw_line in stream:
                line = raw_line.rstrip("\r\n")
                stripped = line.strip()
                if not in_body:
                    if stripped == "...":
                        in_body = True
                    continue
                if not stripped or stripped.startswith("#"):
                    continue
                columns = line.split("\t")
                if len(columns) < 2:
                    continue
                text = columns[0].strip()
                code = columns[1].strip()
                if not text or not code:
                    continue
                weight = columns[2].strip() if len(columns) > 2 else ""
                yield DictionaryEntry(text, code, weight, relative)

    def _item(
            self, schema_id: str, path: Path, kind: str,
            entry_paths: tuple[Path, ...], editable: bool = False,
    ) -> DictionaryCatalogItem:
        relative = self._relative(path)
        purpose = self._purpose(relative, path)
        title = purpose.split("（", 1)[0]
        return DictionaryCatalogItem(
            schema_id, title, relative, purpose, kind, path,
            entry_paths, editable,
        )

    def _schema_dictionary_names(self, schema_id: str) -> list[str]:
        result: list[str] = []
        for path in (
            self.root / "build" / f"{schema_id}.schema.yaml",
            self.root / f"{schema_id}.schema.yaml",
            self.root / f"{schema_id}.custom.yaml",
        ):
            data = self._read_yaml(path)
            self._collect_named_values(data, {"dictionary"}, result)
        return list(dict.fromkeys(value for value in result if value))

    def _personal_phrase_paths(self, schema_id: str) -> list[Path]:
        known_roots = {
            name for name, _quality in scheme_dictionary_roots(schema_id)
        }
        names: list[str] = []
        for path in (
            self.root / "build" / f"{schema_id}.schema.yaml",
            self.root / f"{schema_id}.schema.yaml",
            self.root / f"{schema_id}.custom.yaml",
        ):
            self._collect_named_values(
                self._read_yaml(path), {"user_dict"}, names
            )
        if schema_id == "rime_frost" and (self.root / "custom_phrase.txt").is_file():
            names.append("custom_phrase")
        if schema_id == "wanxiang" and (self.root / "wanxiang_user_phrase.txt").is_file():
            names.append("wanxiang_user_phrase")
        paths = []
        for name in dict.fromkeys(names):
            if not name or name in known_roots:
                continue
            filename = name if Path(name).suffix else f"{name}.txt"
            paths.append(self.root / filename)
        return paths

    @staticmethod
    def _collect_named_values(data, keys: set[str], result: list[str]) -> None:
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key) in keys and isinstance(value, str):
                    result.append(value.strip())
                else:
                    DictionaryCatalogService._collect_named_values(value, keys, result)
        elif isinstance(data, list):
            for value in data:
                DictionaryCatalogService._collect_named_values(value, keys, result)

    @staticmethod
    def _read_yaml(path: Path):
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, yaml.YAMLError):
            return {}

    def _dictionary_path(self, name: str) -> Path:
        path = self.root / name
        if path.name.endswith(".dict.yaml"):
            return path
        return path.with_name(path.name + ".dict.yaml")

    def _expand_table(self, root: Path) -> list[Path]:
        seen: set[Path] = set()
        result: list[Path] = []

        def visit(path: Path) -> None:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                return
            seen.add(resolved)
            result.append(path)
            for target in self._import_tables(path):
                visit(self._dictionary_path(target))

        visit(root)
        return result

    @staticmethod
    def _import_tables(path: Path) -> Iterable[str]:
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        except OSError:
            return ()
        values: list[str] = []
        active = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import_tables:"):
                active = True
                continue
            if active and stripped.startswith("-"):
                value = stripped[1:].split("#", 1)[0].strip().strip("\"'")
                if value:
                    values.append(value)
                continue
            if active and stripped and not stripped.startswith("#"):
                break
        return values

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _purpose(relative: str, path: Path) -> str:
        normalized = relative.replace("\\", "/")
        if normalized in _PURPOSES:
            return _PURPOSES[normalized]
        if normalized.startswith("cn_dicts_cell/"):
            purpose = _CELL_PURPOSES.get(path.name.removesuffix(".dict.yaml"))
            if purpose:
                return purpose
        if path.is_dir() and path.name.endswith(".userdb"):
            return "Rime 自动学习用户词典"
        return "扩展词典（用途未识别）"
