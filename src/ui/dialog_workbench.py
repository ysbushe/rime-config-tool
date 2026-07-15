"""Small shared primitives for the application's structured dialogs."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


def dialog_section(parent: QWidget, title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame(parent)
    frame.setObjectName("DialogSection")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(8)
    heading = QLabel(title, frame)
    heading.setObjectName("DialogSectionTitle")
    layout.addWidget(heading)
    return frame, layout
