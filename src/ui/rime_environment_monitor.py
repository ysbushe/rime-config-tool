"""Low-cost monitor for Rime frontend scheme changes."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from src.service.managed_files import ActiveSchemeResolution, resolve_active_scheme


class RimeEnvironmentMonitor(QObject):
    """Poll only tiny state files and emit after the effective state changes."""

    changed = Signal(object)

    def __init__(self, rime_dir_provider, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._provider = rime_dir_provider
        self._signature: tuple = ()
        self._resolution = ActiveSchemeResolution("", None, False, "尚未检测")
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.check_now)

    @property
    def resolution(self) -> ActiveSchemeResolution:
        return self._resolution

    def start(self) -> None:
        self.check_now(force=True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_window_visible(self, visible: bool) -> None:
        self._timer.setInterval(2000 if visible else 10000)

    def check_now(self, force: bool = False) -> ActiveSchemeResolution:
        value = str(self._provider() or "")
        root = Path(value) if value else Path()
        signature = [value]
        for relative in (
            "user.yaml", "default.custom.yaml", "default.yaml", "build/default.yaml",
        ):
            path = root / relative
            try:
                stat = path.stat()
                signature.append((relative, stat.st_size, stat.st_mtime_ns))
            except OSError:
                signature.append((relative, 0, 0))
        current_signature = tuple(signature)
        if not force and current_signature == self._signature:
            return self._resolution
        self._signature = current_signature
        resolution = resolve_active_scheme(value)
        changed = (
            resolution.schema_id != self._resolution.schema_id
            or resolution.writable != self._resolution.writable
            or resolution.reason != self._resolution.reason
        )
        self._resolution = resolution
        if changed:
            self.changed.emit(resolution)
        return resolution
