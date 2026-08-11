"""Bounded, read-only preview shown before restoring a managed backup."""
from __future__ import annotations

import difflib
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)


class RestorePreviewDialog(QDialog):
    MAX_DIFF_LINES = 2000

    def __init__(self, current: Path, backup: Path, filename: str,
                 parent=None) -> None:
        super().__init__(parent)
        current_lines = (
            current.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            if current.is_file() else []
        )
        backup_lines = backup.read_text(
            encoding="utf-8-sig", errors="replace",
        ).splitlines()
        diff = list(difflib.unified_diff(
            current_lines, backup_lines,
            fromfile=f"当前/{filename}", tofile=f"备份/{backup.name}",
            lineterm="",
        ))

        self.setObjectName("AppDialog")
        self.setWindowTitle("恢复前比较")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        added = sum(
            1 for line in diff
            if line.startswith("+") and not line.startswith("+++")
        )
        removed = sum(
            1 for line in diff
            if line.startswith("-") and not line.startswith("---")
        )
        summary = QLabel(
            "没有内容差异。" if not diff
            else f"发现 {added} 行将加入，{removed} 行将移除。",
            self,
        )
        summary.setWordWrap(True)
        summary.setProperty("role", "info")
        layout.addWidget(summary)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        visible = diff[:self.MAX_DIFF_LINES]
        if len(diff) > len(visible):
            visible.append(f"…差异过多，仅显示前 {len(visible)} 行。")
        editor.setPlainText(
            "\n".join(visible) if visible else "当前文件与所选备份内容相同。"
        )
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
