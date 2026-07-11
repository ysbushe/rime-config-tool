"""全局设置（Settings）。

负责：
    - 首次启动自动探测 Rime 目录并记忆到 settings.json
    - 手动覆盖优先于自动探测
    - 其余用户偏好（自启 / 热键 / 自动部署 / 备份份数 / 主题）的读写
"""
from __future__ import annotations

import json
import configparser
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from src.config.paths import settings_path
from src.config.rime_path_detector import RimePathDetector
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 默认值（任何缺失项以此兜底）
_DEFAULTS: Dict[str, Any] = {
    "rime_dir": "",          # Rime 用户目录（手动覆盖 / 自动探测）
    "auto_deploy": False,    # 写文件后是否自动重新部署
    "hotkey_enabled": True,  # 全局热键收藏开关
    "hotkey_combo": "Ctrl+Alt+Q",  # 默认热键组合
    "autostart": False,      # 开机自启
    "backup_count": 5,       # 备份保留份数
    "backup_dir": "",       # 自定义备份目录；空值使用 Rime/.backup
    "theme": "light",        # 主题（风格 A 浅色）
    "sandbox_mode": False,   # 沙盒预览模式：操作副本，不碰真实 Rime 配置
    "auto_group_done": False,  # 首次启动自动分组是否已执行
    "deployer_path": "",     # 用户手动指定的 WeaselDeployer.exe 路径（优先于自动探测）
}

# 允许持久化的字段
_KNOWN_KEYS = tuple(_DEFAULTS.keys())


class Settings:
    """全局设置单例（进程内复用同一实例即可）。"""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = dict(_DEFAULTS)
        self._detector = RimePathDetector()
        self.load()
        self._load_backup_ini()
        # 空路径、失效路径或测试临时目录均重新探测，避免污染真实启动。
        configured = str(self._data.get("rime_dir", ""))
        configured_path = Path(configured) if configured else None
        try:
            under_temp = bool(
                configured_path
                and configured_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
            )
        except (OSError, ValueError):
            under_temp = False
        if not configured_path or not configured_path.is_dir() or under_temp:
            detected = self._detector.detect()
            if detected:
                self._data["rime_dir"] = detected
                self.save()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """从 settings.json 载入；文件不存在或解析失败则保留默认。"""
        p = settings_path()
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in _KNOWN_KEYS:
                        self._data[k] = v
        except Exception as exc:  # 损坏的配置文件不应崩溃
            logger.warning("读取设置失败，使用默认值：%s", exc)

    @staticmethod
    def _backup_ini_path() -> Path:
        return settings_path().with_name("backup.ini")

    def _load_backup_ini(self) -> None:
        path = self._backup_ini_path()
        if not path.is_file():
            return
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(path, encoding="utf-8")
            self._data["backup_dir"] = parser.get("backup", "path", fallback="")
        except Exception as exc:
            logger.warning("读取备份 INI 失败：%s", exc)

    def _save_backup_ini(self) -> None:
        path = self._backup_ini_path()
        parser = configparser.ConfigParser(interpolation=None)
        parser["backup"] = {"path": str(self._data.get("backup_dir", ""))}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                parser.write(handle)
        except Exception as exc:
            logger.warning("保存备份 INI 失败：%s", exc)

    def save(self) -> None:
        """写入 settings.json（仅已知字段）。"""
        p = settings_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("保存设置失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 通用访问器
    # ------------------------------------------------------------------ #
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if key not in _KNOWN_KEYS:
            logger.warning("尝试设置未知配置项：%s（已忽略）", key)
            return
        self._data[key] = value
        self.save()
        if key == "backup_dir":
            self._save_backup_ini()

    # ------------------------------------------------------------------ #
    # Rime 目录（核心）
    # ------------------------------------------------------------------ #
    @property
    def rime_dir(self) -> str:
        return str(self._data.get("rime_dir", ""))

    @rime_dir.setter
    def rime_dir(self, value: str) -> None:
        """手动覆盖 Rime 目录。空字符串表示清除（下次启动重新探测）。"""
        self._data["rime_dir"] = str(value)
        self.save()

    def rime_dir_path(self) -> Optional[Path]:
        d = self._data.get("rime_dir", "")
        return Path(d) if d else None

    def is_rime_dir_valid(self) -> bool:
        p = self.rime_dir_path()
        return bool(p and p.is_dir())

    # ------------------------------------------------------------------ #
    # 便捷布尔 / 数值访问器
    # ------------------------------------------------------------------ #
    @property
    def auto_deploy(self) -> bool:
        return bool(self._data.get("auto_deploy", False))

    @auto_deploy.setter
    def auto_deploy(self, value: bool) -> None:
        self.set("auto_deploy", bool(value))

    @property
    def hotkey_enabled(self) -> bool:
        return bool(self._data.get("hotkey_enabled", True))

    @hotkey_enabled.setter
    def hotkey_enabled(self, value: bool) -> None:
        self.set("hotkey_enabled", bool(value))

    @property
    def hotkey_combo(self) -> str:
        return str(self._data.get("hotkey_combo", "Ctrl+Alt+Q"))

    @hotkey_combo.setter
    def hotkey_combo(self, value: str) -> None:
        self.set("hotkey_combo", str(value))

    @property
    def autostart(self) -> bool:
        return bool(self._data.get("autostart", False))

    @autostart.setter
    def autostart(self, value: bool) -> None:
        self.set("autostart", bool(value))

    @property
    def backup_count(self) -> int:
        try:
            return max(1, int(self._data.get("backup_count", 5)))
        except (TypeError, ValueError):
            return 5

    @backup_count.setter
    def backup_count(self, value: int) -> None:
        self.set("backup_count", max(1, int(value)))

    @property
    def backup_dir(self) -> str:
        return str(self._data.get("backup_dir", ""))

    @backup_dir.setter
    def backup_dir(self, value: str) -> None:
        self.set("backup_dir", str(value))


    @property
    def theme(self) -> str:
        return str(self._data.get("theme", "light"))

    @theme.setter
    def theme(self, value: str) -> None:
        self.set("theme", str(value))

    # ------------------------------------------------------------------ #
    @property
    def sandbox_mode(self) -> bool:
        return bool(self._data.get("sandbox_mode", False))

    @sandbox_mode.setter
    def sandbox_mode(self, value: bool) -> None:
        self.set("sandbox_mode", bool(value))

    # ------------------------------------------------------------------ #
    @property
    def auto_group_done(self) -> bool:
        return bool(self._data.get("auto_group_done", False))

    @auto_group_done.setter
    def auto_group_done(self, value: bool) -> None:
        self.set("auto_group_done", bool(value))

    @property
    def deployer_path(self) -> str:
        return str(self._data.get("deployer_path", ""))

    @deployer_path.setter
    def deployer_path(self, value: str) -> None:
        self.set("deployer_path", str(value))
