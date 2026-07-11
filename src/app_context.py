"""应用上下文（AppContext）。

集中持有全局单例：设置、各仓储、各服务。由 main.py 构建，
传递给 MainWindow / TrayIcon 等，避免散落的全局变量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.repo.phrase_repo import PhraseRepo
from src.repo.schema_repo import SchemaRepo
from src.repo.symbols_repo import SymbolsRepo
from src.service.autostart import Autostart
from src.service.backup_service import BackupService
from src.service.cache_service import CacheService
from src.service.deploy_service import DeployService
from src.service.group_service import GroupService
from src.service.hotkey_manager import HotkeyManager
from src.service.pinyin_service import PinyinService
from src.service.sandbox_service import SandboxService
from src.settings import Settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AppContext:
    """共享应用对象集合。"""

    settings: Settings
    phrase_repo: PhraseRepo
    schema_repo: SchemaRepo
    symbols_repo: SymbolsRepo
    group_service: GroupService
    backup_service: BackupService
    pinyin_service: PinyinService
    deploy_service: DeployService
    hotkey_manager: HotkeyManager
    autostart: Autostart
    cache_service: CacheService
    sandbox_service: SandboxService

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "AppContext":
        """基于当前 Settings 构建全部仓储与服务。"""
        settings = settings or Settings()
        cache = CacheService()
        sandbox = SandboxService(settings)
        # 沙盒开启 → 指向副本目录；否则真实目录（可能为空）
        rime_dir = sandbox.active_rime_dir()

        pinyin = PinyinService()
        backup = BackupService(
            rime_dir, keep=settings.backup_count, backup_dir=settings.backup_dir)
        deploy = DeployService(settings)
        autostart = Autostart()
        hotkey = HotkeyManager()

        # 目录有效 → 指向真实文件；否则给空仓储（UI 提示设置目录）
        if rime_dir:
            phrase_repo = PhraseRepo(f"{rime_dir}/custom_phrase.txt", cache=cache)
            schema_repo = SchemaRepo(f"{rime_dir}/rime_frost.schema.yaml", cache=cache)
            symbols_repo = SymbolsRepo(f"{rime_dir}/symbols_v.yaml", cache=cache)
            group_service = GroupService(rime_dir)
        else:
            logger.warning("Rime 目录未设置，仓储为空，请在设置页指定。")
            phrase_repo = PhraseRepo("", cache=cache)  # 空路径，load 时为空
            schema_repo = SchemaRepo("", cache=cache)
            symbols_repo = SymbolsRepo("", cache=cache)
            group_service = GroupService("")

        return cls(
            settings=settings,
            phrase_repo=phrase_repo,
            schema_repo=schema_repo,
            symbols_repo=symbols_repo,
            group_service=group_service,
            backup_service=backup,
            pinyin_service=pinyin,
            deploy_service=deploy,
            hotkey_manager=hotkey,
            autostart=autostart,
            cache_service=cache,
            sandbox_service=sandbox,
        )

    def rebuild_repos(self) -> None:
        """Rime 目录 / 沙盒模式变化后重建仓储与备份服务。"""
        rime_dir = self.sandbox_service.active_rime_dir()
        cache = self.cache_service
        self.backup_service.rime_dir = rime_dir
        self.backup_service.keep = self.settings.backup_count
        self.backup_service.backup_dir = self.settings.backup_dir
        self.phrase_repo.__init__(f"{rime_dir}/custom_phrase.txt", cache=cache)
        self.schema_repo.__init__(f"{rime_dir}/rime_frost.schema.yaml", cache=cache)
        self.symbols_repo.__init__(f"{rime_dir}/symbols_v.yaml", cache=cache)
        self.group_service.__init__(rime_dir)
