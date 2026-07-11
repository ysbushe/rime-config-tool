"""Rime 用户目录自动探测。

探测优先级（自动探测，不含手动覆盖）：
    1. 注册表（Weasel 写入的 RimeUserDir）
    2. %APPDATA%\\Rime
    3. 环境变量 RIME_USER_DIR

手动覆盖的优先级由 Settings 负责（手动 > 上述自动探测）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)


class RimePathDetector:
    """探测 Rime 用户目录（存放 custom_phrase.txt 等的目录）。"""

    # (根键, 子键, 值名) —— 小狼毫常见写入位置
    _REGISTRY_CANDIDATES: List[Tuple[str, str, str]] = [
        ("HKCU", r"Software\Rime\Weasel", "RimeUserDir"),
        ("HKLM", r"Software\Rime\Weasel", "RimeUserDir"),
        ("HKCU", r"Software\Rime", "RimeUserDir"),
        ("HKLM", r"Software\Rime", "RimeUserDir"),
    ]

    def detect(self) -> Optional[str]:
        """返回探测到的有效目录路径；都失败则返回 None。"""
        # 1) 注册表
        reg_path = self._detect_from_registry()
        if reg_path:
            logger.info("Rime 目录探测（注册表）：%s", reg_path)
            return reg_path

        # 2) %APPDATA%\Rime
        appdata = os.environ.get("APPDATA")
        if appdata:
            cand = Path(appdata) / "Rime"
            if cand.is_dir():
                logger.info("Rime 目录探测（%%APPDATA%%\\Rime）：%s", cand)
                return str(cand)

        # 3) 环境变量
        env = os.environ.get("RIME_USER_DIR")
        if env and Path(env).is_dir():
            logger.info("Rime 目录探测（环境变量 RIME_USER_DIR）：%s", env)
            return str(Path(env))

        logger.warning("未能自动探测到 Rime 目录，需用户手动指定。")
        return None

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _detect_from_registry(self) -> Optional[str]:
        try:
            import winreg  # type: ignore
        except Exception:  # 非 Windows 环境
            return None

        root_map = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
        for root_name, subkey, value in self._REGISTRY_CANDIDATES:
            hkey = root_map.get(root_name)
            if hkey is None:
                continue
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    data, _ = winreg.QueryValueEx(key, value)
                if data and Path(data).is_dir():
                    return str(Path(data))
            except OSError:
                continue
        return None
