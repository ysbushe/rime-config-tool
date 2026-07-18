"""Application entry point."""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.app_context import AppContext  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    if "--rime-preview-host" in sys.argv or os.environ.get("RIME_CONFIG_PREVIEW_HOST") == "1":
        from src.service.rime_preview_host import run
        return run()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from src.service.single_instance import SingleInstanceGuard
    from src.service.windows_notifications import configure_windows_notification_identity

    start_minimized = "--autostart" in sys.argv
    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        restored = instance_guard.request_existing_instance()
        logger.info("已有实例正在运行，已请求显示主窗口：%s", restored)
        return 0

    configure_windows_notification_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("RIME 配置小工具")
    app.setQuitOnLastWindowClosed(False)

    settings = Settings()
    logger.info("Rime 目录：%s", settings.rime_dir or "（未设置，需手动指定）")
    if not settings.is_rime_dir_valid():
        logger.warning("Rime 目录无效或不存在，请在设置页指定。")

    context = AppContext.build(settings)
    window = MainWindow(context, start_minimized=start_minimized)
    if start_minimized:
        # Create the native handle before hiding. A never-shown Qt window may
        # not be recoverable through a later tray or single-instance request.
        window.show()
        window.hide()
        logger.info("应用由开机自启启动，保持托盘常驻。")
    else:
        window.show()

    wake_timer = QTimer(app)

    def restore_existing_window() -> None:
        if instance_guard.consume_wakeup():
            window.show_main()

    wake_timer.timeout.connect(restore_existing_window)
    # IPC activation is not latency-sensitive; halve idle wakeups without a visible delay.
    wake_timer.start(400)

    mode = "开机自启最小化" if start_minimized else "普通启动"
    logger.info("应用已启动（托盘常驻）。模式=%s", mode)
    try:
        return app.exec()
    finally:
        wake_timer.stop()
        instance_guard.release()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.exception("应用异常退出：%s", exc)
        sys.exit(1)
