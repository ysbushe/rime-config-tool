"""pytest 公共夹具。

- 将项目根加入 sys.path（使 import src.xxx 可用）
- 设置 QT_QPA_PLATFORM=offscreen（无显示环境跑 GUI 构造检查）
- 提供临时 Rime 目录（从 fixtures 拷贝）与 AppContext
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

# 无显示环境：必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# 参与测试的真实文件名
PHRASE = "sample_custom_phrase.txt"
SCHEMA = "sample_rime_frost.schema.yaml"
SYMBOLS = "sample_symbols_v.yaml"


@pytest.fixture(scope="session")
def qapp():
    """全局 QApplication（offscreen）。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # 不退出，避免影响其它用例


@pytest.fixture()
def temp_rime_dir(tmp_path) -> Path:
    """构造临时 Rime 目录，拷贝 fixtures 并重命名为真实文件名。"""
    d = tmp_path / "Rime"
    d.mkdir()
    shutil.copy(FIXTURES / PHRASE, d / "custom_phrase.txt")
    shutil.copy(FIXTURES / SCHEMA, d / "rime_frost.schema.yaml")
    shutil.copy(FIXTURES / SYMBOLS, d / "symbols_v.yaml")
    return d


@pytest.fixture()
def app_context(temp_rime_dir, monkeypatch):
    """构建指向临时目录的 AppContext。"""
    from src.app_context import AppContext
    from src.settings import Settings

    settings_file = temp_rime_dir.parent / "settings.json"
    monkeypatch.setattr("src.settings.settings_path", lambda: settings_file)
    settings = Settings()

    settings.rime_dir = str(temp_rime_dir)
    settings.backup_count = 5
    return AppContext.build(settings)


@pytest.fixture()
def phrase_repo(temp_rime_dir):
    from src.repo.phrase_repo import PhraseRepo

    return PhraseRepo(str(temp_rime_dir / "custom_phrase.txt"))


@pytest.fixture()
def backup_service(temp_rime_dir):
    from src.service.backup_service import BackupService

    return BackupService(str(temp_rime_dir), keep=5)
