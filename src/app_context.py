"""应用上下文（AppContext）。

集中持有全局单例：设置、各仓储、各服务。由 main.py 构建，
传递给 MainWindow / TrayIcon 等，避免散落的全局变量。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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
from src.service.library_tools import MetadataStore
from src.service.pinyin_service import PinyinService
from src.service.sandbox_service import SandboxService
from src.service.data_engine_service import DataEngineClient
from src.service.dictionary_catalog import DictionaryCatalogService
from src.service.vnext_dictionary_index import VNextDictionaryIndex
from src.service.file_transaction import TransactionalFileWriter
from src.service.rime_preview_service import RimePreviewService
from src.service.managed_files import (
    ActiveSchemeResolution,
    ManagedSchemeFiles,
    resolve_active_scheme,
    known_managed_schemes,
)
from src.settings import Settings
from src.config.paths import settings_path
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _active_schema_path(rime_dir: str) -> str:
    """Resolve the schema file that the effective enabled list selects."""
    if not rime_dir:
        return ""
    schema_id = DictionaryCatalogService(rime_dir).preferred_scheme()
    return f"{rime_dir}/{schema_id}.schema.yaml" if schema_id else ""


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
    system_dictionary_index: VNextDictionaryIndex
    data_engine_client: DataEngineClient
    dictionary_query_client: DataEngineClient
    index_engine_client: DataEngineClient
    file_transaction_writer: TransactionalFileWriter
    rime_preview_service: RimePreviewService
    metadata_store: MetadataStore
    active_scheme: ActiveSchemeResolution

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "AppContext":
        """基于当前 Settings 构建全部仓储与服务。"""
        settings = settings or Settings()
        cache = CacheService()
        sandbox = SandboxService(settings)
        # 沙盒开启 → 指向副本目录；否则真实目录（可能为空）
        rime_dir = sandbox.active_rime_dir()
        scheme_source = settings.rime_dir if settings.rime_dir else rime_dir
        active_scheme = resolve_active_scheme(scheme_source)
        managed = active_scheme.files

        pinyin = PinyinService()
        policy = settings.scheme_backup_policy(active_scheme.schema_id)
        backup = BackupService(
            rime_dir,
            keep=policy["backup_count"],
            backup_dir=settings.backup_dir,
            namespace=managed.backup_prefix if managed else "unresolved",
        )
        backup.auto_cleanup = settings.backup_auto_cleanup
        deploy = DeployService(settings)
        autostart = Autostart()
        hotkey = HotkeyManager()
        data_engine_client = DataEngineClient(timeout_seconds=15)
        dictionary_query_client = DataEngineClient(timeout_seconds=5)
        index_engine_client = DataEngineClient(timeout_seconds=15)
        file_transaction_writer = TransactionalFileWriter(
            settings_path().parent / "transactions"
        )
        recovered = file_transaction_writer.recover_pending()
        if recovered:
            logger.warning("已恢复 %d 个未完成的文件事务。", len(recovered))
        system_dictionary_index = VNextDictionaryIndex(
            rime_dir,
            dictionary_query_client,
            settings_path().parent / "indexes",
            maintenance_client=index_engine_client,
        )
        rime_preview = RimePreviewService(rime_dir, deploy.deployer_path)
        metadata_store = MetadataStore(rime_dir)

        # 目录有效 → 指向真实文件；否则给空仓储（UI 提示设置目录）
        if rime_dir and managed and active_scheme.writable:
            phrase_repo = PhraseRepo(f"{rime_dir}/{managed.phrase_filename}", cache=cache)
            schema_repo = SchemaRepo(f"{rime_dir}/{managed.schema_filename}", cache=cache)
            symbols_repo = SymbolsRepo(f"{rime_dir}/symbols_v.yaml", cache=cache)
            group_service = GroupService(rime_dir, managed.phrase_filename)
        else:
            logger.warning("Rime 目录或当前方案未确认，仓储保持只读空状态：%s", active_scheme.reason)
            phrase_repo = PhraseRepo("", cache=cache)  # 空路径，load 时为空
            schema_repo = SchemaRepo("", cache=cache)
            symbols_repo = SymbolsRepo("", cache=cache)
            group_service = GroupService("")

        if rime_dir and managed and active_scheme.writable:
            cls._restore_missing_groups(
                rime_dir, managed, phrase_repo, group_service,
                file_transaction_writer,
            )

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
            system_dictionary_index=system_dictionary_index,
            data_engine_client=data_engine_client,
            dictionary_query_client=dictionary_query_client,
            index_engine_client=index_engine_client,
            file_transaction_writer=file_transaction_writer,
            rime_preview_service=rime_preview,
            metadata_store=metadata_store,
            active_scheme=active_scheme,
        )

    def rebuild_repos(self, *, refresh_index: bool = True) -> None:
        """Rime 目录 / 沙盒模式变化后重建仓储与备份服务。"""
        rime_dir = self.sandbox_service.active_rime_dir()
        scheme_source = self.settings.rime_dir if self.settings.rime_dir else rime_dir
        self.active_scheme = resolve_active_scheme(scheme_source)
        managed = self.active_scheme.files
        cache = self.cache_service
        self.backup_service.rime_dir = rime_dir
        policy = self.settings.scheme_backup_policy(self.active_scheme.schema_id)
        self.backup_service.keep = policy["backup_count"]
        self.backup_service.auto_cleanup = self.settings.backup_auto_cleanup
        self.backup_service.backup_dir = self.settings.backup_dir
        self.backup_service.namespace = managed.backup_prefix if managed else "unresolved"
        phrase_path = f"{rime_dir}/{managed.phrase_filename}" if managed and self.active_scheme.writable else ""
        schema_path = f"{rime_dir}/{managed.schema_filename}" if managed and self.active_scheme.writable else ""
        self.phrase_repo.reset(phrase_path, cache=cache)
        self.schema_repo.reset(schema_path, cache=cache)
        self.symbols_repo.reset(f"{rime_dir}/symbols_v.yaml" if rime_dir else "", cache=cache)
        self.group_service.reset(
            rime_dir if managed and self.active_scheme.writable else "",
            managed.phrase_filename if managed else "custom_phrase.txt",
        )
        if rime_dir and managed and self.active_scheme.writable:
            self._restore_missing_groups(
                rime_dir, managed, self.phrase_repo, self.group_service,
                self.file_transaction_writer,
            )
        if refresh_index:
            self.system_dictionary_index.reset(rime_dir)
        self.rime_preview_service.reset(rime_dir, self.deploy_service.deployer_path)
        self.metadata_store = MetadataStore(rime_dir)

    def shutdown(self) -> None:
        """Release process-wide helpers; safe to call more than once."""
        self.hotkey_manager.unregister()
        self.rime_preview_service.close()
        self.data_engine_client.close()
        self.dictionary_query_client.close()
        self.index_engine_client.close()

    @property
    def active_scheme_id(self) -> str:
        return self.active_scheme.schema_id

    @property
    def managed_files(self) -> ManagedSchemeFiles | None:
        return self.active_scheme.files

    def active_scheme_changed(self) -> bool:
        source = self.settings.rime_dir or self.sandbox_service.active_rime_dir()
        return resolve_active_scheme(source).schema_id != self.active_scheme.schema_id

    @staticmethod
    def _restore_missing_groups(
        rime_dir: str,
        managed: ManagedSchemeFiles,
        phrase_repo: PhraseRepo,
        groups: GroupService,
        writer: TransactionalFileWriter,
    ) -> int:
        if groups.path.exists():
            return 0
        valid_keys = {phrase.key for phrase in phrase_repo.all()}
        best_source = None
        best_matches = -1
        root = Path(rime_dir)
        for candidate in known_managed_schemes():
            if candidate.schema_id == managed.schema_id:
                continue
            path = root / candidate.group_filename
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                matches = len(set(dict(raw.get("membership", {}))) & valid_keys)
            except Exception:
                continue
            if matches > best_matches:
                best_source, best_matches = path, matches
        if best_source is None or best_matches <= 0:
            return 0
        return groups.restore_compatible_sidecar(best_source, valid_keys, writer)
