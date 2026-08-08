"""Shared presentation rules for background dictionary-index progress."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class IndexProgressView:
    percent: int
    phase_label: str
    files_completed: int
    files_total: int
    current_file: str


_PHASE_LABELS = {
    "queued": "准备",
    "building": "生成",
    "copying": "复制",
    "optimizing": "优化",
    "switching": "切换",
}


def present_index_progress(progress: Mapping | None) -> IndexProgressView:
    """Convert the engine's transport payload into stable user-facing values."""
    value = progress or {}
    phase = str(value.get("phase", ""))
    completed = _non_negative_int(value.get("files_completed", 0))
    total = _non_negative_int(value.get("files_total", 0))
    total_bytes = _non_negative_int(value.get("total_bytes", 0))
    processed_bytes = _non_negative_int(value.get("processed_bytes", 0))
    if phase == "optimizing":
        percent = 97
    elif phase == "switching":
        percent = 99
    elif total_bytes:
        percent = round(processed_bytes * 95 / total_bytes)
    elif total:
        percent = round(completed * 95 / total)
    else:
        percent = 0
    return IndexProgressView(
        max(0, min(99, percent)),
        _PHASE_LABELS.get(phase, "生成"),
        completed,
        total,
        str(value.get("current_file", "")),
    )


def _non_negative_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
