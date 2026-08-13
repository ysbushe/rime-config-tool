"""Lifecycle and latest-request coordination for the VNext data engine."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="data-engine")
        self._response_queue: queue.Queue[str] | None = None
        self._latest_pending: dict[
            str, tuple[int, str, dict, Callable[[dict | None, Exception | None], None]]
        ] = {}
        self._latest_running: set[str] = set()
        self._closed = False

    def request(self, operation: str, payload: dict | None = None) -> dict:
        with self._lock:
            if self._closed:
                raise DataEngineError("数据引擎客户端已经关闭")
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
        """Keep at most one running and one replaceable pending request per channel."""
        with self._lock:
            if self._closed:
                raise DataEngineError("数据引擎客户端已经关闭")
            generation = self._generations.get(channel, 0) + 1
            self._generations[channel] = generation
            self._latest_pending[channel] = (
                generation, operation, dict(payload), callback,
            )
            if channel not in self._latest_running:
                self._latest_running.add(channel)
                self._executor.submit(self._drain_latest_channel, channel)
        return generation

    def _drain_latest_channel(self, channel: str) -> None:
        while True:
            with self._lock:
                request = self._latest_pending.pop(channel, None)
                if request is None or self._closed:
                    self._latest_running.discard(channel)
                    return
            generation, operation, payload, callback = request
            result = None
            error = None
            try:
                result = self.request(operation, payload)
            except Exception as exc:  # delivered to the UI boundary
                error = exc
            with self._lock:
                current = (
                    not self._closed
                    and generation == self._generations.get(channel)
                )
            if current:
                try:
                    callback(result, error)
                except Exception:
                    # UI-bound callbacks must not strand the per-channel worker.
                    pass

    def cancel_pending(self, channel: str | None = None) -> None:
        with self._lock:
            if channel is None:
                for key in tuple(self._generations):
                    self._generations[key] += 1
                self._latest_pending.clear()
            else:
                self._generations[channel] = self._generations.get(channel, 0) + 1
                self._latest_pending.pop(channel, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.cancel_pending()
        self.stop_worker()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def stop_worker(self) -> None:
        """Stop the disposable child while keeping this client reusable."""
        with self._io_lock:
            process = self._process
            self._process = None
            self._response_queue = None
            if process is not None:
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
            response = json.loads(self._read_line(self._timeout_seconds))
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
                "sharded_lookup_text", "sharded_lookup_code",
                "sharded_dictionary_entries_page",
                "indexed_dictionary_entries_page",
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
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            ),
        )
        response_queue: queue.Queue[str] = queue.Queue()
        self._response_queue = response_queue

        def read_responses(process=self._process, destination=response_queue) -> None:
            assert process.stdout
            while True:
                line = process.stdout.readline()
                destination.put(line)
                if not line:
                    return

        threading.Thread(
            target=read_responses, daemon=True, name="data-engine-reader",
        ).start()
        return self._process

    def _terminate(self) -> None:
        process, self._process = self._process, None
        self._response_queue = None
        if process is not None and process.poll() is None:
            process.terminate()

    def _read_line(self, timeout: float) -> str:
        result = self._response_queue
        if result is None:
            raise DataEngineError("数据引擎读取通道不可用")
        try:
            line = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("数据引擎响应超时") from exc
        if not line:
            raise DataEngineError("数据引擎进程已退出")
        return line
