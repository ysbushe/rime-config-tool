"""Lifecycle and latest-request coordination for the VNext data engine."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from typing import Callable

from src.service.data_engine_host import PROTOCOL_VERSION


class DataEngineError(RuntimeError):
    pass


class DataEngineClient:
    """Own one disposable worker and keep stale async results out of the UI."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._generations: dict[str, int] = {}
        self._timeout_seconds = timeout_seconds

    def request(self, operation: str, payload: dict | None = None) -> dict:
        with self._io_lock:
            return self._request_locked(operation, payload or {}, retry=True)

    def request_latest(
        self,
        operation: str,
        payload: dict,
        callback: Callable[[dict | None, Exception | None], None],
        *,
        channel: str = "default",
    ) -> int:
        """Run asynchronously and suppress a result superseded by a newer request."""
        with self._lock:
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation

        def work() -> None:
            result = None
            error = None
            try:
                result = self.request(operation, payload)
            except Exception as exc:  # delivered to the UI boundary
                error = exc
            with self._lock:
                current = generation == self._generations.get(channel)
            if current:
                callback(result, error)

        threading.Thread(
            target=work, daemon=True, name=f"data-engine-{operation}",
        ).start()
        return generation

    def cancel_pending(self, channel: str | None = None) -> None:
        with self._lock:
            if channel is None:
                for key in tuple(self._generations):
                    self._generations[key] += 1
            else:
                self._generations[channel] = self._generations.get(channel, 0) + 1

    def close(self) -> None:
        self.cancel_pending()
        with self._io_lock:
            process = self._process
            self._process = None
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin:
                    self._request_id += 1
                    process.stdin.write(json.dumps({
                        "version": PROTOCOL_VERSION,
                        "id": self._request_id,
                        "operation": "shutdown",
                        "payload": {},
                    }) + "\n")
                    process.stdin.flush()
                    process.wait(timeout=1)
            except Exception:
                process.terminate()

    def _request_locked(self, operation: str, payload: dict, retry: bool) -> dict:
        process = self._ensure_process()
        self._request_id += 1
        request_id = self._request_id
        try:
            assert process.stdin and process.stdout
            process.stdin.write(json.dumps({
                "version": PROTOCOL_VERSION,
                "id": request_id,
                "operation": operation,
                "payload": payload,
            }, ensure_ascii=True) + "\n")
            process.stdin.flush()
            response = json.loads(self._read_line(process.stdout, self._timeout_seconds))
            if response.get("version") != PROTOCOL_VERSION:
                raise DataEngineError("数据引擎返回了不兼容的协议版本")
            if response.get("id") != request_id:
                raise DataEngineError("数据引擎响应编号不匹配")
            if not response.get("ok"):
                raise DataEngineError(str(response.get("error", "数据引擎请求失败")))
            return dict(response.get("result") or {})
        except Exception:
            self._terminate()
            if retry and operation in {
                "hello", "ping", "lookup_text", "lookup_code",
                "lookup_text_page", "lookup_code_page", "dictionary_snapshot",
                "index_task_status", "cancel_index_task", "dictionary_entries_page",
            }:
                return self._request_locked(operation, payload, retry=False)
            raise

    def _ensure_process(self) -> subprocess.Popen:
        if self._process is not None and self._process.poll() is None:
            return self._process
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--data-engine-host"]
        else:
            args = [
                sys.executable, "-c",
                "from src.service.data_engine_host import run; raise SystemExit(run())",
            ]
        self._process = subprocess.Popen(
            args,
            cwd=str(Path(__file__).resolve().parents[2]),
            text=True,
            encoding="utf-8",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return self._process

    def _terminate(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()

    @staticmethod
    def _read_line(stream, timeout: float) -> str:
        result: queue.Queue[str] = queue.Queue(maxsize=1)
        threading.Thread(
            target=lambda: result.put(stream.readline()), daemon=True,
        ).start()
        try:
            line = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("数据引擎响应超时") from exc
        if not line:
            raise DataEngineError("数据引擎进程已退出")
        return line
