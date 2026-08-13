"""Lazy, isolated Rime candidate preview service."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Optional

from PySide6.QtCore import QObject, Signal

from src.encoding.code_suggestions import raw_code
from src.config.paths import user_config_dir
from src.service.rime_profiles import safe_preview_schema
from src.service.rime_preview_workspace import PreviewWorkspace
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _configured_page_size(rime_dir: str, default: int = 5) -> int:
    """Read Rime's deployed menu size without requiring a YAML dependency."""
    root = Path(rime_dir) if rime_dir else None
    if root is None:
        return default
    for path in (root / "build/default.yaml", root / "default.yaml"):
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        in_menu = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line[:1].isspace():
                in_menu = stripped == "menu:"
                continue
            if in_menu and stripped.startswith("page_size:"):
                raw_value = stripped.split(":", 1)[1].split("#", 1)[0].strip()
                try:
                    return max(1, min(10, int(raw_value)))
                except ValueError:
                    break
    return default


class PreviewEvents(QObject):
    changed = Signal()


@dataclass(frozen=True)
class PreviewCandidate:
    text: str
    comment: str = ""


@dataclass(frozen=True)
class PreviewSnapshot:
    state: str
    code: str = ""
    candidates: tuple[PreviewCandidate, ...] = ()
    message: str = ""


class RimePreviewService:
    """Talk to a disposable child process that owns a librime preview session."""

    def __init__(self, rime_dir: str = "", deployer_path: str = "") -> None:
        self._lock = threading.RLock()
        self.events = PreviewEvents()
        self._rime_dir = rime_dir
        self._page_size = _configured_page_size(rime_dir)
        self._deployer_path = deployer_path
        self._schema_id, self._schema_message = safe_preview_schema(rime_dir)
        self._workspace = PreviewWorkspace(user_config_dir() / "preview")
        self._preview_dir = ""
        self._prebuilt_data_dir = ""
        self._workspace_message = ""
        self._process: subprocess.Popen | None = None
        self._response_queue: queue.Queue[str] | None = None
        self._snapshot = PreviewSnapshot("idle")
        self._request_id = 0
        self._queued_code = ""
        self._busy = False
        self._idle_timer: threading.Timer | None = None
        # Keep the preview host warm while the user edits, then release librime.
        self._idle_timeout_seconds = 10 * 60

    def _cancel_idle_close_locked(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _schedule_idle_close_locked(self) -> None:
        self._cancel_idle_close_locked()
        if self._process is None:
            return
        timer = threading.Timer(self._idle_timeout_seconds, self._close_when_idle)
        timer.daemon = True
        self._idle_timer = timer
        timer.start()

    def _close_when_idle(self) -> None:
        with self._lock:
            self._idle_timer = None
            if self._busy:
                return
        self.close()

    def _set_snapshot(self, snapshot: PreviewSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
        self.events.changed.emit()

    @property
    def snapshot(self) -> PreviewSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def schema_id(self) -> str | None:
        return self._schema_id

    @property
    def availability_message(self) -> str:
        return self._schema_message

    @property
    def page_size(self) -> int:
        return self._page_size


    def reset(self, rime_dir: str, deployer_path: str = "") -> None:
        self.close()
        with self._lock:
            self._request_id += 1
            self._queued_code = ""
            self._rime_dir = rime_dir
            self._page_size = _configured_page_size(rime_dir)
            self._deployer_path = deployer_path
            self._snapshot = PreviewSnapshot("idle")
            self._schema_id, self._schema_message = safe_preview_schema(rime_dir)
            self._preview_dir = ""
            self._prebuilt_data_dir = ""
            self._workspace_message = ""
        self.events.changed.emit()

    def mark_waiting_for_deploy(self) -> None:
        with self._lock:
            self._request_id += 1
            self._queued_code = ""
            self._snapshot = PreviewSnapshot("waiting_deploy", message="配置已保存，等待重新部署后刷新候选预览。")
        self.events.changed.emit()

    def invalidate_after_deploy(self, *, warm_up: bool = False) -> None:
        """Drop stale preview state without competing with Weasel by default."""
        self.close()
        with self._lock:
            self._request_id += 1
            self._snapshot = PreviewSnapshot("stale", message="已重新部署，候选预览服务正在刷新。")
        self.events.changed.emit()
        if warm_up:
            self.warm_up_async()

    def warm_up_async(self) -> None:
        """Initialize librime in the background so the first dialog opens promptly."""
        with self._lock:
            if self._busy or not self._rime_dir:
                return
            if not self._schema_id:
                self._snapshot = PreviewSnapshot("unavailable", message=self._schema_message)
                self.events.changed.emit()
                return
            self._busy = True
        threading.Thread(target=self._warm_up, daemon=True, name="rime-preview-warmup").start()

    def _warm_up(self) -> None:
        started = time.perf_counter()
        try:
            process = self._ensure_process()
            payload = {
                "command": "warmup", "rime_dir": self._preview_dir,
                "rime_dll": str(self._rime_dll()), "schema_id": self._schema_id,
                "prebuilt_data_dir": self._prebuilt_data_dir,
            }
            assert process.stdin and process.stdout
            process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
            process.stdin.flush()
            result = json.loads(self._read_line(timeout=12))
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "候选预览初始化失败"))
            logger.info(
                "Rime 候选预览预热完成 elapsed_ms=%.1f",
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            logger.info("Rime 候选预览预热失败：%s", exc)
            self.close()
        finally:
            with self._lock:
                pending = bool(self._queued_code) and self._snapshot.state != "waiting_deploy"
                self._busy = False
            if pending:
                self.request(self._queued_code)

    def request(self, code: str) -> None:
        code = raw_code(code)
        with self._lock:
            self._cancel_idle_close_locked()
            self._request_id += 1
            self._queued_code = code
            if not code:
                self._snapshot = PreviewSnapshot("idle")
                self.events.changed.emit()
                return
            if self._snapshot.state == "waiting_deploy":
                return
            if not self._schema_id:
                self._snapshot = PreviewSnapshot("unavailable", code, (), self._schema_message)
                self.events.changed.emit()
                return
            self._snapshot = PreviewSnapshot("loading" if self._process is None else "querying", code=code)
            self.events.changed.emit()
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._query, daemon=True, name="rime-preview-query").start()

    def close(self) -> None:
        with self._lock:
            self._cancel_idle_close_locked()
            process, self._process = self._process, None
            self._response_queue = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                process.stdin.flush()
            process.wait(timeout=1)
        except Exception:
            process.terminate()

    def _query(self) -> None:
        # Coalesce rapid typing: only the newest code is displayed or queried next.
        while True:
            with self._lock:
                request_id = self._request_id
                code = self._queued_code
                self._snapshot = PreviewSnapshot("querying", code=code)
            self.events.changed.emit()
            started = time.perf_counter()
            try:
                process = self._ensure_process()
                payload = {
                    "command": "preview", "code": code, "rime_dir": self._preview_dir,
                    "rime_dll": str(self._rime_dll()), "schema_id": self._schema_id,
                    "prebuilt_data_dir": self._prebuilt_data_dir,
                }
                assert process.stdin and process.stdout
                process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
                process.stdin.flush()
                line = self._read_line(timeout=12)
                result = json.loads(line)
                if not result.get("ok"):
                    raise RuntimeError(result.get("error", "候选预览不可用"))
                candidates = tuple(PreviewCandidate(**item) for item in result.get("candidates", []))
                snapshot = PreviewSnapshot(
                    "ready", code, candidates, self._workspace_message,
                )
                logger.info(
                    "Rime 候选预览查询完成 code_length=%d elapsed_ms=%.1f",
                    len(code), (time.perf_counter() - started) * 1000,
                )
            except Exception as exc:
                logger.info("Rime 候选预览不可用：%s", exc)
                snapshot = PreviewSnapshot("unavailable", code, (), f"候选预览不可用：{exc}")
                self.close()
            with self._lock:
                if request_id == self._request_id:
                    self._snapshot = snapshot
                    self._busy = False
                    self._schedule_idle_close_locked()
                    self.events.changed.emit()
                    return
                # Saving without deployment and repository resets cancel any in-flight lookup.
                if not self._queued_code or self._snapshot.state in {"waiting_deploy", "idle"}:
                    self._busy = False
                    self._schedule_idle_close_locked()
                    return
                # The input changed during this query; loop once for the newest code.

    def _ensure_process(self) -> subprocess.Popen:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self._process
            dll = self._rime_dll()
            if not self._rime_dir or not dll.is_file():
                raise RuntimeError("未找到可用的 Rime 预览引擎。")
            if not self._schema_id:
                raise RuntimeError(self._schema_message or "未识别可预览方案。")
            report = self._workspace.prepare(self._rime_dir, self._schema_id)
            self._preview_dir = str(report.path)
            self._prebuilt_data_dir = str(Path(self._rime_dir) / "build")
            if report.skipped_userdbs:
                self._workspace_message = (
                    "部分学习库正在被 Rime 占用，本次预览未包含："
                    + "、".join(report.skipped_userdbs)
                )
            else:
                self._workspace_message = ""
            if getattr(sys, "frozen", False):
                args = [sys.executable, "--rime-preview-host"]
            else:
                args = [
                    sys.executable,
                    "-c",
                    "from src.service.rime_preview_host import run; raise SystemExit(run())",
                ]
            preview_env = dict(__import__("os").environ)
            preview_env["RIME_CONFIG_PREVIEW_HOST"] = "1"
            started = time.perf_counter()
            self._process = subprocess.Popen(
                args, cwd=str(Path(__file__).resolve().parents[2]), env=preview_env, text=True,
                encoding="utf-8", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                ),
            )
            response_queue: queue.Queue[str] = queue.Queue()
            self._response_queue = response_queue

            def read_responses(process=self._process,
                               destination=response_queue) -> None:
                assert process.stdout
                while True:
                    line = process.stdout.readline()
                    destination.put(line)
                    if not line:
                        return

            threading.Thread(
                target=read_responses, daemon=True, name="rime-preview-reader",
            ).start()
            logger.info(
                "Rime 候选预览子进程已启动 pid=%s elapsed_ms=%.1f",
                self._process.pid, (time.perf_counter() - started) * 1000,
            )
            return self._process

    def _rime_dll(self) -> Path:
        if self._deployer_path:
            candidate = Path(self._deployer_path).parent / "rime.dll"
            if candidate.is_file():
                return candidate
        roots = [Path.home() / "AppData/Local/Programs/Rime", Path("C:/Program Files/Rime")]
        for root in roots:
            if root.is_dir():
                matches = sorted(root.glob("weasel-*/rime.dll"), reverse=True)
                if matches:
                    return matches[0]
        return Path()

    def _read_line(self, timeout: float) -> str:
        result = self._response_queue
        if result is None:
            raise RuntimeError("Rime 预览引擎读取通道不可用")
        try:
            line = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("Rime 预览引擎加载超时") from exc
        if not line:
            raise RuntimeError("Rime 预览引擎已退出")
        return line
