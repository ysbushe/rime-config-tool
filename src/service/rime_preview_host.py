"""Isolated librime candidate-preview host.

Runs as a child process so a failed DLL load cannot affect the GUI or Weasel.
The protocol is JSON-lines on standard input/output.
"""
from __future__ import annotations

import ctypes as c
import json
import os
from pathlib import Path
import queue
import sys
import threading


class _Traits(c.Structure):
    _fields_ = [
        ("data_size", c.c_int), ("shared_data_dir", c.c_char_p),
        ("user_data_dir", c.c_char_p), ("distribution_name", c.c_char_p),
        ("distribution_code_name", c.c_char_p), ("distribution_version", c.c_char_p),
        ("app_name", c.c_char_p), ("modules", c.POINTER(c.c_char_p)),
        ("min_log_level", c.c_int), ("log_dir", c.c_char_p),
        ("prebuilt_data_dir", c.c_char_p), ("staging_dir", c.c_char_p),
    ]


class _Composition(c.Structure):
    _fields_ = [
        ("length", c.c_int), ("cursor_pos", c.c_int),
        ("sel_start", c.c_int), ("sel_end", c.c_int), ("preedit", c.c_char_p),
    ]


class _Candidate(c.Structure):
    _fields_ = [("text", c.c_char_p), ("comment", c.c_char_p), ("reserved", c.c_void_p)]


class _Menu(c.Structure):
    _fields_ = [
        ("page_size", c.c_int), ("page_no", c.c_int), ("is_last_page", c.c_int),
        ("highlighted_candidate_index", c.c_int), ("num_candidates", c.c_int),
        ("candidates", c.POINTER(_Candidate)), ("select_keys", c.c_char_p),
    ]


class _Context(c.Structure):
    _fields_ = [
        ("data_size", c.c_int), ("composition", _Composition), ("menu", _Menu),
        ("commit_text_preview", c.c_char_p), ("select_labels", c.POINTER(c.c_char_p)),
    ]


class PreviewEngine:
    def __init__(self, rime_dir: str, rime_dll: str, schema_id: str) -> None:
        self._root = Path(rime_dll).parent
        self._rime_dir = Path(rime_dir)
        self._schema_id = schema_id
        self._session = 0
        self._lib = None

    def start(self) -> None:
        if self._lib is not None:
            return
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(self._root))
        lib = c.WinDLL(str(self._root / "rime.dll"))
        specs = [
            ("RimeSetup", None, [c.POINTER(_Traits)]),
            ("RimeInitialize", None, [c.POINTER(_Traits)]),
            ("RimeCreateSession", c.c_size_t, []),
            ("RimeSelectSchema", c.c_int, [c.c_size_t, c.c_char_p]),
            ("RimeClearComposition", None, [c.c_size_t]),
            ("RimeSimulateKeySequence", c.c_int, [c.c_size_t, c.c_char_p]),
            ("RimeGetContext", c.c_int, [c.c_size_t, c.POINTER(_Context)]),
            ("RimeFreeContext", c.c_int, [c.POINTER(_Context)]),
            ("RimeDestroySession", c.c_int, [c.c_size_t]),
            ("RimeFinalize", None, []),
        ]
        for name, restype, argtypes in specs:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
        traits = _Traits()
        traits.data_size = c.sizeof(_Traits) - c.sizeof(c.c_int)
        traits.shared_data_dir = str(self._root / "data").encode()
        traits.user_data_dir = str(self._rime_dir).encode()
        traits.distribution_name = b"RimeConfig Preview"
        traits.distribution_code_name = b"rime_config_preview"
        traits.distribution_version = b"1"
        traits.app_name = b"rime.config_preview"
        traits.min_log_level = 1
        traits.log_dir = b""
        lib.RimeSetup(c.byref(traits))
        lib.RimeInitialize(c.byref(traits))
        self._session = int(lib.RimeCreateSession())
        if not self._session or not lib.RimeSelectSchema(self._session, self._schema_id.encode()):
            raise RuntimeError(f"无法载入方案：{self._schema_id}")
        self._lib = lib

    def preview(self, code: str) -> list[dict[str, str]]:
        self.start()
        assert self._lib is not None
        self._lib.RimeClearComposition(self._session)
        if not self._lib.RimeSimulateKeySequence(self._session, code.encode()):
            return []
        context = _Context()
        context.data_size = c.sizeof(_Context) - c.sizeof(c.c_int)
        if not self._lib.RimeGetContext(self._session, c.byref(context)):
            return []
        try:
            result = []
            for index in range(min(5, int(context.menu.num_candidates))):
                candidate = context.menu.candidates[index]
                result.append({
                    "text": (candidate.text or b"").decode("utf-8", "replace"),
                    "comment": (candidate.comment or b"").decode("utf-8", "replace"),
                })
            return result
        finally:
            self._lib.RimeFreeContext(c.byref(context))

    def close(self) -> None:
        if self._lib is None:
            return
        try:
            if self._session:
                self._lib.RimeDestroySession(self._session)
            self._lib.RimeFinalize()
        finally:
            self._session = 0
            self._lib = None


def run() -> int:
    """Serve preview requests; leave after three minutes without a request."""
    incoming: queue.Queue[str | None] = queue.Queue()

    def read_stdin() -> None:
        for line in sys.stdin:
            incoming.put(line)
        incoming.put(None)

    threading.Thread(target=read_stdin, daemon=True).start()
    engine: PreviewEngine | None = None
    try:
        while True:
            try:
                line = incoming.get(timeout=180)
            except queue.Empty:
                return 0
            if line is None:
                return 0
            try:
                request = json.loads(line)
                if request.get("command") == "shutdown":
                    return 0
                if engine is None:
                    engine = PreviewEngine(request["rime_dir"], request["rime_dll"], request.get("schema_id", "rime_frost"))
                if request.get("command") == "warmup":
                    engine.start()
                    response = {"ok": True, "candidates": [], "warmed": True}
                else:
                    candidates = engine.preview(str(request.get("code", "")))
                    response = {"ok": True, "candidates": candidates}
            except Exception as exc:
                response = {"ok": False, "error": str(exc)}
            print(json.dumps(response, ensure_ascii=True), flush=True)
    finally:
        if engine is not None:
            engine.close()


if __name__ == "__main__":
    raise SystemExit(run())
