"""Versioned JSON-lines host for the VNext dictionary data engine."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading
import uuid

from src.service.compact_dictionary_index import (
    CompactDictionaryIndex,
    CompactIndexBuildCancelled,
    discover_dictionary_sources,
)
from src.service.dictionary_catalog import (
    dictionary_catalog_snapshot,
    input_code,
)
from src.service.pinyin_service import PinyinService


PROTOCOL_VERSION = 1


class _IndexTaskManager:
    """Run one index mutation at a time while the protocol remains responsive."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._active_task_id: str | None = None

    def start(self, payload: dict, pinyin: PinyinService) -> dict:
        index_path = str(payload["index_path"])
        scheme_id = str(payload["scheme_id"])
        rime_dir = str(payload["rime_dir"])
        roots = _parse_roots(payload.get("roots") or [])
        sources = discover_dictionary_sources(rime_dir, roots)
        if not sources:
            raise ValueError("未发现可构建索引的词典源文件")

        with self._lock:
            if self._active_task_id is not None:
                active = self._tasks.get(self._active_task_id, {})
                if active.get("status") in {"queued", "running", "cancelling"}:
                    raise ValueError("已有索引任务正在运行")
            task_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            self._active_task_id = task_id
            self._cancel_events[task_id] = cancel_event
            self._tasks[task_id] = {
                "task_id": task_id,
                "status": "queued",
                "scheme_id": scheme_id,
                "index_path": index_path,
                "progress": {
                    "phase": "queued", "files_completed": 0,
                    "files_total": len(sources), "current_file": "",
                    "current_entries": 0,
                    "processed_bytes": 0,
                    "total_bytes": sum(item.path.stat().st_size for item in sources),
                },
                "report": None,
                "error": None,
            }

        def update_progress(value: dict) -> None:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task["progress"] = dict(value)

        def work() -> None:
            with self._lock:
                self._tasks[task_id]["status"] = "running"
            try:
                report = CompactDictionaryIndex(index_path, scheme_id).refresh(
                    sources,
                    pinyin.split_pinyin_code,
                    progress=update_progress,
                    should_cancel=cancel_event.is_set,
                )
                report_value = asdict(report)
                report_value["path"] = str(report.path)
                with self._lock:
                    self._tasks[task_id]["status"] = "completed"
                    self._tasks[task_id]["report"] = report_value
            except CompactIndexBuildCancelled:
                with self._lock:
                    self._tasks[task_id]["status"] = "cancelled"
            except Exception as exc:
                with self._lock:
                    self._tasks[task_id]["status"] = "failed"
                    self._tasks[task_id]["error"] = str(exc)
            finally:
                with self._lock:
                    if self._active_task_id == task_id:
                        self._active_task_id = None

        threading.Thread(
            target=work, daemon=True, name=f"index-build-{scheme_id}",
        ).start()
        return self.status(task_id)

    def status(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("索引任务不存在或数据引擎已重启")
            return {
                **task,
                "progress": dict(task["progress"]),
                "report": dict(task["report"]) if task["report"] else None,
            }

    def cancel(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("索引任务不存在或数据引擎已重启")
            if task["status"] in {"queued", "running"}:
                task["status"] = "cancelling"
                self._cancel_events[task_id].set()
        return self.status(task_id)

    def cancel_active(self) -> None:
        with self._lock:
            if self._active_task_id is not None:
                self._cancel_events[self._active_task_id].set()


def run() -> int:
    indexes: dict[tuple[str, str], CompactDictionaryIndex] = {}
    pinyin = PinyinService()
    tasks = _IndexTaskManager()
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            if request.get("version") != PROTOCOL_VERSION:
                raise ValueError("数据引擎协议版本不匹配")
            operation = request.get("operation")
            payload = request.get("payload") or {}
            if operation == "shutdown":
                tasks.cancel_active()
                _write({"id": request_id, "ok": True, "result": {"stopped": True}})
                return 0
            if operation in {"hello", "ping"}:
                result = {"protocol_version": PROTOCOL_VERSION, "state": "ready"}
            elif operation == "dictionary_snapshot":
                result = _dictionary_snapshot(str(payload.get("rime_dir", "")))
            elif operation == "dictionary_entries_page":
                result = _dictionary_entries_page(payload)
            elif operation == "start_index_refresh":
                result = tasks.start(payload, pinyin)
            elif operation == "index_task_status":
                result = tasks.status(str(payload.get("task_id", "")))
            elif operation == "cancel_index_task":
                result = tasks.cancel(str(payload.get("task_id", "")))
            elif operation in {
                "lookup_text", "lookup_code", "lookup_text_page", "lookup_code_page",
            }:
                path = str(payload["index_path"])
                scheme_id = str(payload["scheme_id"])
                index = indexes.setdefault(
                    (path, scheme_id), CompactDictionaryIndex(path, scheme_id),
                )
                limit = max(1, min(500, int(payload.get("limit", 20))))
                if operation == "lookup_text":
                    rows = index.lookup_text(str(payload.get("query", "")), limit)
                    result = {"candidates": [asdict(row) for row in rows]}
                elif operation == "lookup_code":
                    rows = index.lookup_code(
                        str(payload.get("query", "")), limit,
                        bool(payload.get("include_shorthand", True)),
                        pinyin.split_pinyin_code,
                    )
                    result = {"candidates": [asdict(row) for row in rows]}
                elif operation == "lookup_text_page":
                    page = index.lookup_text_page(
                        str(payload.get("query", "")), limit,
                        _optional_cursor(payload.get("cursor")),
                    )
                    result = {
                        "candidates": [asdict(row) for row in page.candidates],
                        "next_cursor": page.next_cursor,
                    }
                else:
                    page = index.lookup_code_page(
                        str(payload.get("query", "")), limit,
                        _optional_cursor(payload.get("cursor")),
                        bool(payload.get("include_shorthand", True)),
                        pinyin.split_pinyin_code,
                    )
                    result = {
                        "candidates": [asdict(row) for row in page.candidates],
                        "next_cursor": page.next_cursor,
                    }
            else:
                raise ValueError(f"不支持的数据引擎操作：{operation}")
            _write({"id": request_id, "ok": True, "result": result})
        except Exception as exc:
            _write({"id": request_id, "ok": False, "error": str(exc)})
    return 0


def _write(payload: dict) -> None:
    payload["version"] = PROTOCOL_VERSION
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def _parse_roots(value: list) -> list[tuple[str, float]]:
    roots = []
    for item in value:
        if isinstance(item, dict):
            name = str(item.get("name", ""))
            quality = float(item.get("quality", 1.0))
        else:
            name = str(item[0])
            quality = float(item[1])
        if name:
            roots.append((name, quality))
    if not roots:
        raise ValueError("索引任务缺少根词典")
    return roots


def _optional_cursor(value) -> int | None:
    if value is None or value == "":
        return None
    cursor = int(value)
    if cursor < 0:
        raise ValueError("分页游标无效")
    return cursor


def _dictionary_snapshot(rime_dir: str) -> dict:
    return dictionary_catalog_snapshot(rime_dir)


def _dictionary_entries_page(payload: dict) -> dict:
    root = Path(str(payload.get("rime_dir", ""))).resolve()
    if not root.is_dir():
        raise ValueError("Rime 目录无效")
    paths = []
    for value in payload.get("paths") or []:
        path = Path(str(value)).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("词典路径超出 Rime 目录") from exc
        if path.is_file():
            paths.append(path)
    limit = max(1, min(500, int(payload.get("limit", 200))))
    needle = str(payload.get("query", "")).strip().casefold()
    cursor = payload.get("cursor") or {}
    start_file = max(0, int(cursor.get("file_index", 0)))
    start_line = int(cursor.get("line_number", -1))
    accepted = []
    for file_index, path in enumerate(paths):
        if file_index < start_file:
            continue
        in_body = path.suffix.casefold() == ".txt"
        with path.open("r", encoding="utf-8-sig", errors="ignore") as stream:
            for line_number, raw_line in enumerate(stream):
                line = raw_line.rstrip("\r\n")
                stripped = line.strip()
                if not in_body:
                    if stripped == "...":
                        in_body = True
                    continue
                if file_index == start_file and line_number <= start_line:
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
                if needle and needle not in {
                    text.casefold(), input_code(code).casefold(),
                }:
                    continue
                accepted.append({
                    "text": text,
                    "code": code,
                    "weight": columns[2].strip() if len(columns) > 2 else "",
                    "source_file": path.relative_to(root).as_posix(),
                    "cursor": {"file_index": file_index, "line_number": line_number},
                })
                if len(accepted) > limit:
                    page = accepted[:limit]
                    next_cursor = page[-1].pop("cursor")
                    for item in page:
                        item.pop("cursor", None)
                    return {"entries": page, "next_cursor": next_cursor}
    for item in accepted:
        item.pop("cursor", None)
    return {"entries": accepted, "next_cursor": None}


if __name__ == "__main__":
    raise SystemExit(run())
