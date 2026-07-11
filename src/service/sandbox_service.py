"""沙盒预览模式（SandboxService）。

开启后，程序把受管文件（custom_phrase.txt / rime_frost.schema.yaml /
symbols_v.yaml / custom_phrase.txt.groups.json）复制到用户配置目录下的 sandbox/
子目录，并以该副本作为工作目录。一切读写（含分组 sidecar、备份 .backup）
都发生在副本内，绝不触碰用户真实的 Rime 配置。

符合项目铁律：测试/预览不得修改本地 Rime 已有配置；必须为测试/预览行为时，
复制受管文件到副本内操作。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from src.config.paths import (
    PHRASE_FILENAME,
    SCHEMA_FILENAME,
    SYMBOLS_FILENAME,
    group_sidecar_path,
    user_config_dir,
)
from src.settings import Settings

_MANAGED_FILES = (PHRASE_FILENAME, SCHEMA_FILENAME, SYMBOLS_FILENAME)


class SandboxService:
    """沙盒副本管理。"""

    SANDBOX_SUBDIR = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._prepared_source: str | None = None

    def sandbox_dir(self) -> Path:
        return user_config_dir() / self.SANDBOX_SUBDIR

    def active_rime_dir(self) -> str:
        """返回当前应使用的 Rime 目录。

        沙盒开启且真实目录有效时，复制受管文件到沙盒副本并返回副本目录；
        否则返回真实目录。
        """
        real = self._settings.rime_dir
        if not (self._settings.sandbox_mode and real and Path(real).is_dir()):
            self._prepared_source = None
            return real

        real_dir = Path(real)
        source_key = str(real_dir.resolve())
        force_refresh = self._prepared_source != source_key
        sb = self.sandbox_dir()
        sb.mkdir(parents=True, exist_ok=True)
        copy_pairs = [(real_dir / fn, sb / fn) for fn in _MANAGED_FILES]
        sidecar = group_sidecar_path(real_dir, PHRASE_FILENAME)
        copy_pairs.append((sidecar, sb / sidecar.name))
        copy_pairs.append((real_dir / "pinyin_display.ini", sb / "pinyin_display.ini"))
        for src, dst in copy_pairs:
            if src.exists() and (
                    force_refresh
                    or not dst.exists()
                    or dst.stat().st_mtime < src.stat().st_mtime):
                shutil.copy2(src, dst)
            elif force_refresh and not src.exists() and dst.exists():
                # A fresh sandbox must not retain sidecars from another source.
                dst.unlink()
        self._prepared_source = source_key
        return str(sb)

    def is_active(self) -> bool:
        """当前是否处于沙盒模式（副本目录存在）。"""
        return bool(self._settings.sandbox_mode) and self.sandbox_dir().is_dir()
