"""日志工具。

统一使用模块级 logger，输出到 stdout 并（在桌面环境下）写入日志文件。
开发 / 测试阶段不强制创建日志文件，仅保证 import 安全。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_file_handler: Optional[logging.Handler] = None
_file_handler_failed = False


def _shared_file_handler() -> Optional[logging.Handler]:
    """懒加载文件 handler；所有模块 logger 共享同一个 app.log 输出。"""
    global _file_handler, _file_handler_failed
    if _file_handler is not None or _file_handler_failed:
        return _file_handler
    try:
        from src.config.paths import app_log_path

        log_path: Path = app_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT))
        fh.setLevel(logging.INFO)
        _file_handler = fh
    except Exception:
        _file_handler_failed = True
    return _file_handler


def _ensure_file_handler(logger: logging.Logger) -> None:
    """确保每个模块 logger 都写入用户配置目录下的 logs/app.log。"""
    fh = _shared_file_handler()
    if fh is not None and all(handler is not fh for handler in logger.handlers):
        logger.addHandler(fh)


def get_logger(name: str = "rime_config") -> logging.Logger:
    """获取（或创建）命名 logger，幂等。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        formatter = logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(formatter)
        sh.setLevel(logging.INFO)
        logger.addHandler(sh)

    # 桌面环境再补一个文件 handler；即使 logger 已存在也要确保补齐。
    _ensure_file_handler(logger)
    return logger


def set_level(level: int, name: str = "rime_config") -> None:
    """动态调整根 logger 的级别（如测试时调成 WARNING）。"""
    get_logger(name).setLevel(level)
