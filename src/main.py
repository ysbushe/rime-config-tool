"""Application entry point."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.app_context import AppContext  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.config.paths import user_config_dir  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def _update_completion_request(
    argv: list[str],
) -> tuple[str, str, Path] | None:
    """Read and validate the private restart arguments emitted by the updater."""
    try:
        completed_at = argv.index("--update-completed")
        marker_at = argv.index("--update-marker")
        from_version = argv[completed_at + 1].strip()
        to_version = argv[completed_at + 2].strip()
        marker_name = argv[marker_at + 1].strip()
    except (ValueError, IndexError, OSError):
        return None
    updates = (user_config_dir() / "updates").resolve()
    if not marker_name or Path(marker_name).name != marker_name:
        return None
    marker = (updates / marker_name).resolve()
    if marker.parent != updates or not marker.name.startswith("RimeConfig-") \
            or marker.suffix != ".complete":
        return None
    if not from_version or not to_version or len(from_version) > 32 or len(to_version) > 32:
        return None
    return from_version, to_version, marker


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
    update_completion = _update_completion_request(sys.argv)
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
        update_completion_pending=update_completion is not None,
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

    if update_completion is not None:
        from_version, to_version, completion_marker = update_completion
        completion_poll = QTimer(app)
        completion_poll.setInterval(250)
        completion_checks = 0

        def show_update_result_when_confirmed() -> None:
            nonlocal completion_checks
            completion_checks += 1
            if completion_marker.is_file():
                completion_poll.stop()
                completion_marker.unlink(missing_ok=True)
                window.show_update_completed_notice(from_version, to_version)
                return
            if completion_checks >= 120:
                completion_poll.stop()
                logger.warning("新版本已启动，但更新器未写入完成标记：%s", completion_marker)
                window.show_update_completion_unconfirmed()

        completion_poll.timeout.connect(show_update_result_when_confirmed)
        completion_poll.start()

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
