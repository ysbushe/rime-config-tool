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


def _prewarm_quick_add_dialog(context: AppContext) -> None:
    """Move Qt's one-time dialog stylesheet cost before the main window appears."""
    from time import perf_counter
    from PySide6.QtCore import QCoreApplication, QEvent
    from src.ui.quick_add_dialog import QuickAddDialog

    started = perf_counter()
    dialog = QuickAddDialog(
        groups=context.group_service.list_groups(),
        pinyin=context.pinyin_service,
        repo=context.phrase_repo,
        system_dictionary_index=context.system_dictionary_index,
        rime_preview_service=context.rime_preview_service,
        create_group=context.group_service.add_group,
        code_alphabet=context.schema_repo.speller_alphabet(),
    )
    dialog.dispose()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    logger.info(
        "快速收藏：启动期皮肤预热 elapsed_ms=%.1f",
        (perf_counter() - started) * 1000,
    )

def main() -> int:
    if "--input-diagnostics" in sys.argv:
        from src.diagnostics_main import main as diagnostics_main
        return diagnostics_main()
    if "--rime-preview-host" in sys.argv or os.environ.get("RIME_CONFIG_PREVIEW_HOST") == "1":
        from src.service.rime_preview_host import run
        return run()
    if "--data-engine-host" in sys.argv:
        from src.service.data_engine_host import run
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
    gui_test = os.environ.get("RIME_CONFIG_GUI_TEST") == "1"
    window = MainWindow(
        context,
        start_minimized=start_minimized,
        background_tasks_enabled=not gui_test,
    )
    _prewarm_quick_add_dialog(context)
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

    mode = ("GUI测试" if gui_test else
            "开机自启最小化" if start_minimized else "普通启动")
    logger.info("应用已启动（托盘常驻）。模式=%s", mode)
    try:
        return app.exec()
    finally:
        wake_timer.stop()
        context.shutdown()
        instance_guard.release()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.exception("应用异常退出：%s", exc)
        sys.exit(1)
