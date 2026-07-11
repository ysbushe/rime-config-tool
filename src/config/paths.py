"""全局路径常量与用户配置目录解析。

约定：
    - 用户级配置（settings.json / logs）放在 %LOCALAPPDATA%/RimeConfig
    - 备份目录：<rime_dir>/.backup
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

APP_NAME = "RimeConfig"
APP_DISPLAY_NAME = "RIME 配置小工具"

# 三大受管文件名
PHRASE_FILENAME = "custom_phrase.txt"
SCHEMA_FILENAME = "rime_frost.schema.yaml"
SYMBOLS_FILENAME = "symbols_v.yaml"

# 分组 sidecar 文件名（基于词库文件名推导）
BACKUP_DIR_NAME = ".backup"
GROUP_SIDECAR_SUFFIX = ".groups.json"

# 默认备份保留份数
DEFAULT_BACKUP_KEEP = 5


def _base_config_dir() -> Path:
    """用户配置基目录：优先 LOCALAPPDATA，回退 APPDATA / 用户主目录。"""
    for env_key in ("LOCALAPPDATA", "APPDATA"):
        val = os.environ.get(env_key)
        if val:
            return Path(val) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def user_config_dir() -> Path:
    """返回（并创建）用户配置目录。"""
    p = _base_config_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    """settings.json 路径。"""
    return user_config_dir() / "settings.json"


def app_log_path() -> Path:
    """日志文件路径。"""
    return user_config_dir() / "logs" / "app.log"


def group_sidecar_path(rime_dir: Union[str, Path], phrase_filename: str = PHRASE_FILENAME) -> Path:
    """分组 sidecar 文件路径，例如 <rime_dir>/custom_phrase.txt.groups.json。"""
    return Path(rime_dir) / f"{phrase_filename}{GROUP_SIDECAR_SUFFIX}"


def backup_dir(rime_dir: Union[str, Path]) -> Path:
    """<rime_dir>/.backup 目录。"""
    return Path(rime_dir) / BACKUP_DIR_NAME
