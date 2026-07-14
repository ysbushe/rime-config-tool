"""程序入口（main）。

负责：构造 QApplication、构建 AppContext（含目录探测日志）、创建并展示
MainWindow 与系统托盘。运行：python src/main.py
"""
from __future__ import annotations

import os
import sys

# 将项目根目录加入 sys.path，使 `import src.xxx` 在 python src/main.py 时可用
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.app_context import AppContext  # noqa: E402
from src.settings import Settings  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def main() -> int:
    if "--rime-preview-host" in sys.argv:
        from src.service.rime_preview_host import run
        return run()
    from PySide6.QtWidgets import QApplication

    from src.service.single_instance import SingleInstanceGuard
    from src.service.windows_notifications import configure_windows_notification_identity

    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        logger.info("已有 RIME 配置小工具实例正在运行，忽略重复启动。")
        return 0

    configure_windows_notification_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("RIME 配置小工具")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口仅最小化到托盘

    # 设置 + 目录探测日志
    settings = Settings()
    logger.info("Rime 目录：%s", settings.rime_dir or "（未设置，需手动指定）")
    if not settings.is_rime_dir_valid():
        logger.warning("Rime 目录无效或不存在，请在设置页指定。")

    context = AppContext.build(settings)

    window = MainWindow(context)
    window.show()

    logger.info("应用已启动（托盘常驻）。")
    try:
        return app.exec()
    finally:
        instance_guard.release()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.exception("应用异常退出：%s", exc)
        sys.exit(1)
