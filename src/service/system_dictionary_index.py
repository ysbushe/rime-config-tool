"""后台建立 Rime 系统词典的可查询索引。

系统词典通常由多个 import_tables 组合而成，逐次收藏时扫描源文件会让
采集窗口明显停顿。这里在后台将当前 Frost 配置的静态词典写入 SQLite，
前台只按“文本 + 编码”查询。它提供的是静态候选参考，不尝试复刻 Rime
运行时的用户学习、过滤器与翻译器排序。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterable

from src.encoding.code_suggestions import raw_code
from src.config.paths import user_config_dir
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DictionaryCandidate:
    text: str
    code: str
    source: str
    weight: int | None
    quality: float


class SystemDictionaryIndex:
    """异步维护当前 Rime 静态系统词典的轻量查询索引。"""

    _INDEX_VERSION = 3  # Bump when indexed code normalization or schema changes.
    _SOURCES = (("rime_frost", 1.2), ("melt_eng", 1.1))

    def __init__(self, rime_dir: str = "") -> None:
        self._lock = threading.RLock()
        self._rime_dir = Path(rime_dir) if rime_dir else None
        self._state = "unavailable"
        self._thread: threading.Thread | None = None
        self._db_path = user_config_dir() / "system_dictionary_index.sqlite"

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def reset(self, rime_dir: str) -> None:
        with self._lock:
            self._rime_dir = Path(rime_dir) if rime_dir else None
            self._state = "unavailable"
        self.ensure_ready_async()

    def ensure_ready_async(self) -> None:
        with self._lock:
            if not self._rime_dir or not self._rime_dir.is_dir():
                self._state = "unavailable"
                return
            if self._thread and self._thread.is_alive():
                return
            sources = self._source_files()
            if not sources:
                self._state = "unavailable"
                return
            signature = self._signature(sources)
            if self._is_current(signature):
                self._state = "ready"
                return
            self._state = "building"
            self._thread = threading.Thread(
                target=self._build, args=(sources, signature), daemon=True,
                name="RimeSystemDictionaryIndex",
            )
            self._thread.start()

    def lookup(self, text: str, codes: Iterable[str]) -> list[DictionaryCandidate]:
        """返回同文本、同编码的系统词典项；索引未就绪时立即返回空。"""
        normalized = sorted({raw_code(code) for code in codes if raw_code(code)})
        if not text or not normalized or self.state != "ready" or not self._db_path.exists():
            return []
        placeholders = ",".join("?" for _ in normalized)
        try:
            with sqlite3.connect(self._db_path) as db:
                rows = db.execute(
                    f"SELECT text, code, source, weight, quality FROM entries "
                    f"WHERE text = ? AND code IN ({placeholders}) "
                    "ORDER BY quality DESC, COALESCE(weight, 0) DESC",
                    [text, *normalized],
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("读取系统词典索引失败：%s", exc)
            return []
        return [DictionaryCandidate(*row) for row in rows]

    def lookup_code(self, code: str, limit: int = 20) -> list[DictionaryCandidate]:
        """返回某编码的词典项，用于给实时预览候选补充原始权重。"""
        normalized = raw_code(code)
        if not normalized or self.state != "ready" or not self._db_path.exists():
            return []
        try:
            with sqlite3.connect(self._db_path) as db:
                rows = db.execute(
                    "SELECT text, code, source, weight, quality FROM entries "
                    "WHERE code = ? ORDER BY quality DESC, COALESCE(weight, 0) DESC LIMIT ?",
                    (normalized, max(1, limit)),
                ).fetchall()
        except sqlite3.Error as exc:
            logger.warning("读取系统词典同码索引失败：%s", exc)
            return []
        return [DictionaryCandidate(*row) for row in rows]

    def rebuild_sync(self) -> None:
        """测试和维护入口：同步完成一次构建。"""
        sources = self._source_files()
        if not sources:
            with self._lock:
                self._state = "unavailable"
            return
        with self._lock:
            self._state = "building"
        self._build(sources, self._signature(sources))

    def _source_files(self) -> list[tuple[Path, str, float]]:
        if not self._rime_dir:
            return []
        found: list[tuple[Path, str, float]] = []
        for name, quality in self._SOURCES:
            root = self._rime_dir / f"{name}.dict.yaml"
            if not root.is_file():
                continue
            for path in self._expand_table(root):
                found.append((path, name, quality))
        # 同一文件被多个导入链引用时只取一次，保留首次来源。
        unique: dict[Path, tuple[Path, str, float]] = {}
        for item in found:
            unique.setdefault(item[0], item)
        return list(unique.values())

    def _expand_table(self, root: Path) -> list[Path]:
        seen: set[Path] = set()
        result: list[Path] = []

        def visit(path: Path) -> None:
            path = path.resolve()
            if path in seen or not path.is_file():
                return
            seen.add(path)
            result.append(path)
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                return
            in_imports = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("import_tables:"):
                    in_imports = True
                    continue
                if in_imports and stripped.startswith("-"):
                    target = stripped[1:].split("#", 1)[0].strip().strip("\"'")
                    if target:
                        child = self._rime_dir / target
                        if not child.suffixes[-2:] == [".dict", ".yaml"]:
                            child = child.with_name(child.name + ".dict.yaml")
                        visit(child)
                    continue
                if in_imports and stripped and not line[:1].isspace():
                    in_imports = False

        visit(root)
        return result

    @staticmethod
    def _signature(sources: list[tuple[Path, str, float]]) -> str:
        details = []
        for path, source, quality in sources:
            stat = path.stat()
            details.append((SystemDictionaryIndex._INDEX_VERSION, str(path), stat.st_size, stat.st_mtime_ns, source, quality))
        return json.dumps(details, ensure_ascii=True, separators=(",", ":"))

    def _is_current(self, signature: str) -> bool:
        if not self._db_path.exists():
            return False
        try:
            with sqlite3.connect(self._db_path) as db:
                row = db.execute("SELECT value FROM metadata WHERE key = 'signature'").fetchone()
            return bool(row and row[0] == signature)
        except sqlite3.Error:
            return False

    def _build(self, sources: list[tuple[Path, str, float]], signature: str) -> None:
        temporary = self._db_path.with_suffix(".sqlite.tmp")
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            if temporary.exists():
                temporary.unlink()
            db = sqlite3.connect(temporary)
            try:
                db.execute("PRAGMA journal_mode = OFF")
                db.execute("PRAGMA synchronous = OFF")
                db.execute("CREATE TABLE entries (text TEXT, code TEXT, source TEXT, weight INTEGER, quality REAL)")
                db.execute("CREATE INDEX idx_entries_text_code ON entries(text, code)")
                db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
                for path, source, quality in sources:
                    db.executemany(
                        "INSERT INTO entries VALUES (?, ?, ?, ?, ?)",
                        self._read_entries(path, source, quality),
                    )
                db.execute("INSERT INTO metadata VALUES ('signature', ?)", (signature,))
                db.commit()
            finally:
                db.close()
            os.replace(temporary, self._db_path)
            with self._lock:
                self._state = "ready"
            logger.info("系统词典索引就绪：%d 个源文件", len(sources))
        except Exception as exc:
            logger.warning("构建系统词典索引失败：%s", exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            with self._lock:
                self._state = "failed"

    @staticmethod
    def _read_entries(path: Path, source: str, quality: float):
        """Yield rows while reading, avoiding a second full dictionary copy in memory."""
        try:
            handle = path.open("r", encoding="utf-8", errors="ignore")
        except OSError:
            return
        with handle:
            in_data = False
            for line in handle:
                if line.strip() == "...":
                    in_data = True
                    continue
                if not in_data or not line or line.lstrip().startswith("#"):
                    continue
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 2:
                    continue
                text, code = parts[0].strip(), raw_code(parts[1].strip()).replace(" ", "")
                if not text or not code:
                    continue
                try:
                    weight = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip() else None
                except ValueError:
                    weight = None
                yield (text, code, source, weight, quality)
