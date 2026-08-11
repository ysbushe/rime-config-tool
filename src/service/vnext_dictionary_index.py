"""Scheme-scoped adapter from UI lookup APIs to the VNext data engine."""
from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Callable, Iterable

from src.encoding.code_suggestions import contains_han
from src.service.data_engine_service import DataEngineClient
from src.service.dictionary_catalog import DictionaryCatalogService, input_code
from src.service.rime_profiles import scheme_dictionary_roots
from src.service.system_dictionary_index import DictionaryCandidate
from src.utils.logger import get_logger


logger = get_logger(__name__)


class VNextDictionaryIndex:
    """Preserve the current lookup surface while moving storage out of process."""

    handles_shorthand = True

    def __init__(
        self,
        rime_dir: str,
        client: DataEngineClient,
        index_dir: str | Path,
    ) -> None:
        self._lock = threading.RLock()
        self._client = client
        self._index_dir = Path(index_dir)
        self._rime_dir = str(rime_dir or "")
        self._scheme_id = ""
        self._index_path: Path | None = None
        self._task_id = ""
        self._state = "unavailable"
        self._progress: dict = {}
        self._generation = 0
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def progress(self) -> dict:
        with self._lock:
            return dict(self._progress)

    @property
    def scheme_id(self) -> str:
        with self._lock:
            return self._scheme_id

    def cancel_build(self) -> bool:
        """Request cancellation of the current mutation without blocking the GUI."""
        with self._lock:
            task_id = self._task_id
            if self._state != "building" or not task_id:
                return False
            self._state = "cancelling"
        threading.Thread(
            target=self._cancel_task,
            args=(task_id,),
            daemon=True,
            name="cancel-vnext-index",
        ).start()
        return True

    def reset(self, rime_dir: str) -> None:
        with self._lock:
            previous_task = self._task_id
            self._generation += 1
            self._rime_dir = str(rime_dir or "")
            self._scheme_id = ""
            self._index_path = None
            self._task_id = ""
            self._state = "unavailable"
            self._progress = {}
        if previous_task:
            threading.Thread(
                target=self._cancel_task,
                args=(previous_task,),
                daemon=True,
                name="cancel-vnext-index",
            ).start()
        self.ensure_ready_async()

    def ensure_ready_async(self) -> None:
        with self._lock:
            if not self._rime_dir or not Path(self._rime_dir).is_dir():
                self._state = "unavailable"
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._generation += 1
            generation = self._generation
            rime_dir = self._rime_dir
            self._state = "building"
            self._thread = threading.Thread(
                target=self._ensure_worker,
                args=(generation, rime_dir),
                daemon=True,
                name="vnext-dictionary-index",
            )
            self._thread.start()

    def lookup(self, text: str, codes: Iterable[str]) -> list[DictionaryCandidate]:
        normalized_codes = {input_code(code) for code in codes if input_code(code)}
        return [
            item for item in self.lookup_text(text, limit=500)
            if input_code(item.code) in normalized_codes
        ]

    def lookup_text(self, text: str, limit: int = 20) -> list[DictionaryCandidate]:
        return self._lookup_page("lookup_text_page", text, limit)

    def lookup_code(self, code: str, limit: int = 20) -> list[DictionaryCandidate]:
        return self._lookup_page("lookup_code_page", input_code(code), limit)

    def lookup_generated_code(
        self,
        code: str,
        _splitter: Callable[[str], list[str]],
        limit: int = 20,
    ) -> list[DictionaryCandidate]:
        return self.lookup_code(code, limit)

    def lookup_exact(self, value: str, limit: int = 100) -> list[DictionaryCandidate]:
        if contains_han(value):
            return self.lookup_text(value, limit)
        return self.lookup_code(value, limit)

    def _lookup_page(
        self, operation: str, query: str, limit: int,
    ) -> list[DictionaryCandidate]:
        with self._lock:
            if self._state != "ready" or self._index_path is None or not self._scheme_id:
                return []
            path = self._index_path
            scheme_id = self._scheme_id
        try:
            result = self._client.request(operation, {
                "index_path": str(path),
                "scheme_id": scheme_id,
                "query": str(query or ""),
                "limit": max(1, min(500, int(limit))),
                "include_shorthand": True,
            })
        except Exception as exc:
            logger.warning("VNext 词典查询失败：%s", exc)
            return []
        return [
            DictionaryCandidate(
                str(item.get("text", "")), str(item.get("code", "")),
                str(item.get("source", "")),
                int(item["weight"]) if item.get("weight") is not None else None,
                float(item.get("quality", 1.0)),
            )
            for item in result.get("candidates") or []
        ]

    def _ensure_worker(self, generation: int, rime_dir: str) -> None:
        try:
            catalog = DictionaryCatalogService(rime_dir)
            scheme_id = catalog.preferred_scheme()
            roots = [list(item) for item in scheme_dictionary_roots(scheme_id)]
            if not roots:
                self._set_terminal(generation, "unavailable")
                return
            index_path = self._index_dir / f"{scheme_id}.sqlite"
            started = self._client.request("start_index_refresh", {
                "index_path": str(index_path),
                "scheme_id": scheme_id,
                "rime_dir": rime_dir,
                "roots": roots,
            })
            task_id = str(started["task_id"])
            with self._lock:
                if generation != self._generation:
                    self._cancel_task(task_id)
                    return
                self._scheme_id = scheme_id
                self._index_path = index_path
                self._task_id = task_id
                self._progress = dict(started.get("progress") or {})
            while True:
                status = self._client.request(
                    "index_task_status", {"task_id": task_id}
                )
                with self._lock:
                    if generation != self._generation:
                        self._cancel_task(task_id)
                        return
                    self._progress = dict(status.get("progress") or {})
                state = str(status.get("status", ""))
                if state == "completed":
                    self._set_terminal(generation, "ready")
                    return
                if state in {"failed", "cancelled"}:
                    logger.warning("VNext 索引任务结束：%s %s", state, status.get("error", ""))
                    self._set_terminal(generation, state)
                    return
                time.sleep(0.4)
        except Exception as exc:
            logger.warning("VNext 索引启动失败：%s", exc)
            self._set_terminal(generation, "failed")

    def _set_terminal(self, generation: int, state: str) -> None:
        with self._lock:
            if generation == self._generation:
                self._state = state
                self._task_id = ""

    def _cancel_task(self, task_id: str) -> None:
        try:
            self._client.request("cancel_index_task", {"task_id": task_id})
        except Exception:
            return
