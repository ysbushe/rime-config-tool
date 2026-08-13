"""词库管理页（PhraseManager）。

组合：分组侧栏 + 搜索/排序工具栏 + 词库表格。
负责：增删改查、冲突高亮、分组归属、内联编辑、自动分组，并在写回前走 BackupService。

交互要点（本轮改造）：
    - 文本 / 编码 / 权重 三列支持双击内联编辑，编辑先落内存，需点『保存』才写盘并部署；
    - 最左『选择』列复选框 + 『全选』+『批量删除』实现批量勾选删除（删除前确认）；
    - 『保存』写盘+重新部署（沙盒模式下仅写副本、不部署）；『撤销』放弃未保存修改；
    - 『自动分组』按钮仅对未分组条目归类（首次启动自动运行一次并弹窗提示）；
    - 分组列双击弹出下拉框选择已有分组；
    - 视图签名短路 + 文件缓存：数据未变化时跳过全量表格重建，消除切换选项卡卡顿。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QSizePolicy,
    QSplitter,
    QWidget,
)

from src.encoding.code_input import validate_code_input
from src.encoding.code_suggestions import normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.backup_service import BackupService
from src.service.classification_service import ClassificationService
from src.service.deploy_service import DeployService
from src.service.deploy_coordinator import AsyncDeployCoordinator
from src.service.group_service import GroupService
from src.service.library_tools import MetadataStore, health_check
from src.service.pinyin_service import PinyinService
from src.service.pinyin_display_store import PinyinDisplayStore
from src.service.phrase_bundle_writer import PhraseBundleWriter
from src.service.managed_files import known_managed_schemes, resolve_active_scheme
from src.service.system_dictionary_index import SystemDictionaryIndex
from src.service.wanxiang_import import DirectSchemeImportService, WanxiangImportService
from src.service.file_transaction import TransactionalFileWriter
from src.service.file_transaction import FileChange, capture_revision
from src.settings import Settings
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.delete_confirm_dialog import confirm_delete
from src.ui.dictionary_center_dialog import DictionaryCenterDialog
from src.ui.code_delete_dialog import CodeDeleteDialog
from src.ui.group_panel import GroupPanel
from src.ui.history_dialogs import DeploymentStatusDialog, HistoryDialog
from src.ui.library_dialogs import (
    DuplicateManagerDialog, LibraryHealthDialog, PhraseSearchResultDialog,
)
from src.ui.wanxiang_import_dialog import WanxiangImportDialog
from src.ui.phrase_editor import PhraseEditor
from src.ui.multi_code_editor import MultiCodeEditor
from src.ui.quick_tune_dialog import QuickTuneDialog
from src.ui.phrase_table import (
    COL_CODE,
    COL_GROUP,
    COL_TEXT,
    COL_WEIGHT,
    PhraseTableView,
)
from src.ui.search_bar import SearchBar


class PhraseManager(QWidget):
    """词库管理页容器。"""

    favoriteCompleted = Signal(bool, str)
    editCompleted = Signal(bool, str)
    manualDeploymentCompleted = Signal(bool, str)

    PHRASE_FILE = "custom_phrase.txt"
    MIN_WEIGHT = 1
    MAX_WEIGHT = 99

    def __init__(self, repo: PhraseRepo, groups: GroupService,
                 backup: BackupService, settings: Settings,
                 deploy: DeployService,
                 pinyin: PinyinService | None = None,
                 system_dictionary_index: SystemDictionaryIndex | None = None,
                 rime_preview_service=None,
                 metadata_store: MetadataStore | None = None,
                 parent: QWidget | None = None,
                 data_engine_client=None,
                 transaction_writer: TransactionalFileWriter | None = None,
                 code_alphabet: str = "") -> None:
        super().__init__(parent)
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._settings = settings
        self._deploy = deploy
        self._pinyin = pinyin or PinyinService()
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._data_engine_client = data_engine_client
        self._transaction_writer = transaction_writer
        self._code_alphabet = code_alphabet
        self._bundle_writer = PhraseBundleWriter(backup, transaction_writer)
        self._metadata = metadata_store or MetadataStore(repo.path.parent)
        self._last_backup_files: dict[str, str] = {}
        self._display_store = PinyinDisplayStore(self._repo.path.parent, self._pinyin)
        self._quick_filter = "全部"
        self._duplicate_only = False

        self._keyword = ""
        self._current_group = GroupService.all_group_label()
        self._sort_key = "order"
        self._sort_reverse = True
        self._all_selected = False

        self._displayed: List[Phrase] = []   # 当前表格展示的条目（与行号对应）
        self._all_phrases: List[Phrase] = []  # 全量词库（缓存，显隐模式复用）
        self._dirty = False                   # 是否存在未保存的内联编辑
        self._content_sig = None             # 内容签名（用于判断是否需重建 model）
        self._last_sort_mode = None          # 排序未变化时不重排整份词库
        self._visibility_key = None          # 过滤条件未变化时不重复逐行显隐
        self._batch_mode = False             # 批量选择模式（默认关闭，隐藏选择列）
        self._deploy_coordinator = AsyncDeployCoordinator(deploy)
        self._manual_deploy_requested = False
        self._deploy_poll = QTimer(self)
        self._deploy_poll.setInterval(100)
        self._deploy_poll.timeout.connect(self._drain_deploy_results)
        self._auto_deploy_timer = QTimer(self)
        self._auto_deploy_timer.setSingleShot(True)
        self._auto_deploy_timer.setInterval(1200)
        self._auto_deploy_timer.timeout.connect(self._start_auto_deploy)

        self._build_ui()
        self.refresh_scheme_status()
        self.refresh()
        self._maybe_first_run_auto_group()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(12)

        # 页面级信息只负责说明“这里是什么”；高频主动作固定在右上角。
        page_header = QWidget(self)
        page_header.setObjectName("PageHeader")
        header_layout = QHBoxLayout(page_header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)
        title_column = QVBoxLayout()
        title_column.setSpacing(3)
        page_title = QLabel("词库工作台", page_header)
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel("管理个人词条、分组与候选顺序", page_header)
        page_subtitle.setObjectName("PageSubtitle")
        title_column.addWidget(page_title)
        title_column.addWidget(page_subtitle)
        header_layout.addLayout(title_column)
        header_layout.addStretch(1)
        self._scheme_summary = QLabel("正在识别当前方案…", page_header)
        self._scheme_summary.setObjectName("SchemeSummary")
        self._scheme_summary.setProperty("kind", "pill")
        self._scheme_summary.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred,
        )
        self._scheme_refresh = QPushButton("刷新", page_header)
        self._scheme_refresh.setObjectName("TertiaryAction")
        self._scheme_refresh.setToolTip("重新读取 Rime 的启用方案和上次使用方案")
        self._scheme_refresh.clicked.connect(self.refresh_scheme_status)
        header_layout.addWidget(self._scheme_summary)
        header_layout.addWidget(self._scheme_refresh)

        self._btn_add = QPushButton("+ 新增词条", page_header)
        self._btn_add.setObjectName("Primary")
        self._btn_add.clicked.connect(self._on_add)
        header_layout.addWidget(self._btn_add)
        root.addWidget(page_header)

        # 命令栏只保留高频筛选和工作入口；维护类动作收进“词库工具”。
        command_bar = QWidget(self)
        command_bar.setObjectName("CommandBar")
        command_layout = QHBoxLayout(command_bar)
        command_layout.setContentsMargins(10, 8, 10, 8)
        command_layout.setSpacing(8)
        self._search = SearchBar()
        self._search.setMinimumWidth(280)
        self._search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search.searchChanged.connect(self._on_search)
        self._filter_combo = ClickActivatedComboBox()
        self._filter_combo.addItems(["全部", "未分组", "英文", "低权重"])
        self._filter_combo.setToolTip("筛选当前列表")
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        self._sort_combo = ClickActivatedComboBox()
        self._sort_combo.addItems(["权重", "拼音", "文本", "加入顺序倒序"])
        self._sort_combo.setCurrentText("加入顺序倒序")
        self._sort_combo.setToolTip("排序方式")
        self._sort_combo.currentTextChanged.connect(self._on_sort_changed)
        self._btn_batch_select = QPushButton("批量选择")
        self._btn_batch_select.setObjectName("SecondaryAction")
        self._btn_batch_select.clicked.connect(self._on_toggle_batch_select)

        self._btn_quick_tune = QPushButton("快速调节")
        self._btn_quick_tune.setToolTip("打开字词与拼音双向查询和候选排序窗口")
        self._btn_quick_tune.clicked.connect(self._on_quick_tune)
        self._btn_saved_filters = QPushButton("常用筛选", command_bar)
        self._saved_filters_menu = QMenu(self._btn_saved_filters)
        self._saved_filters_menu.aboutToShow.connect(self._rebuild_saved_filters_menu)
        self._btn_saved_filters.setMenu(self._saved_filters_menu)

        command_layout.addWidget(self._search, 1)
        command_layout.addWidget(self._filter_combo)
        command_layout.addWidget(self._sort_combo)
        command_layout.addWidget(self._btn_saved_filters)
        command_layout.addWidget(self._btn_quick_tune)

        # 保留现有按钮对象和信号，菜单仅改变呈现层级，不改变业务入口。
        self._btn_duplicates = QPushButton("显示重码字符/文本", self)
        self._btn_duplicates.setToolTip("扫描整个词库的同文本多编码和同编码多文本项目")
        self._btn_duplicates.clicked.connect(self._open_duplicates)
        self._btn_history = QPushButton("修改历史", self)
        self._btn_history.clicked.connect(self._open_history)
        self._btn_import = QPushButton("从其他方案导入", self)
        self._btn_import.setToolTip("无损扫描并将白霜全拼自定义词库导入万象独立词库")
        self._btn_import.clicked.connect(self._open_scheme_import)
        self._btn_autogroup = QPushButton("自动分组", self)
        self._btn_autogroup.clicked.connect(self._on_auto_group)
        for hidden_action in (
            self._btn_duplicates, self._btn_history,
            self._btn_import, self._btn_autogroup,
        ):
            hidden_action.hide()
        self._btn_tools = QPushButton("词库工具", command_bar)
        tools_menu = QMenu(self._btn_tools)
        tools_menu.addAction("显示重码字符/文本").triggered.connect(
            self._btn_duplicates.click
        )
        tools_menu.addAction("修改历史").triggered.connect(self._btn_history.click)
        tools_menu.addSeparator()
        tools_menu.addAction("从其他方案导入").triggered.connect(
            self._btn_import.click
        )
        tools_menu.addAction("自动分组").triggered.connect(self._btn_autogroup.click)
        self._btn_tools.setMenu(tools_menu)
        command_layout.addWidget(self._btn_tools)
        command_layout.addWidget(self._btn_batch_select)
        root.addWidget(command_bar)

        # 批量动作只有进入批量模式后出现，避免与日常动作竞争注意力。
        self._batch_bar = QWidget(self)
        self._batch_bar.setObjectName("BatchActionBar")
        batch_layout = QHBoxLayout(self._batch_bar)
        batch_layout.setContentsMargins(10, 7, 10, 7)
        batch_layout.setSpacing(8)
        batch_note = QLabel("批量模式：勾选要处理的词条", self._batch_bar)
        batch_note.setObjectName("BatchModeLabel")
        batch_layout.addWidget(batch_note)
        batch_layout.addStretch(1)
        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.clicked.connect(self._on_select_all)
        self._btn_select_all.setVisible(False)
        self._btn_batch_del = QPushButton("批量删除")
        self._btn_batch_del.setObjectName("Danger")
        self._btn_batch_del.clicked.connect(self._on_batch_delete)
        self._btn_batch_del.setVisible(False)
        self._btn_batch_group = QPushButton("调整分组")
        self._btn_batch_group.clicked.connect(self._on_batch_group)
        self._btn_batch_group.setVisible(False)
        batch_layout.addWidget(self._btn_select_all)
        batch_layout.addWidget(self._btn_batch_group)
        batch_layout.addWidget(self._btn_batch_del)
        self._batch_bar.hide()
        root.addWidget(self._batch_bar)

        # 词库内容区：稳定的分组栏 + 主表格。
        self._library_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._library_splitter.setObjectName("LibraryWorkspace")
        self._library_splitter.setChildrenCollapsible(False)
        self._library_splitter.setHandleWidth(8)
        left_widget = QWidget(self._library_splitter)
        left_widget.setObjectName("GroupColumn")
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(12, 12, 8, 12)
        left.setSpacing(8)
        self._group_panel = GroupPanel(
            self._groups,
            count_provider=self._group_counts,
            backup_before_delete=self._backup_before_group_delete,
        )
        self._group_panel.groupSelected.connect(self._on_group_selected)
        self._group_panel.stateChanged.connect(self._on_group_state_changed)
        left.addWidget(self._group_panel, 1)
        left_widget.setMinimumWidth(190)
        left_widget.setMaximumWidth(310)
        self._library_splitter.addWidget(left_widget)

        right_widget = QWidget(self._library_splitter)
        right_widget.setObjectName("TableWorkspace")
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(8, 12, 12, 12)
        right.setSpacing(8)
        table_title = QLabel("词条", right_widget)
        table_title.setObjectName("WorkspaceTitle")
        right.addWidget(table_title)

        self._btn_undo = QPushButton("撤销")
        self._btn_undo.clicked.connect(self._on_undo)
        self._btn_save = QPushButton("保存修改")
        self._btn_save.setObjectName("Primary")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_undo.setEnabled(False)
        self._table = PhraseTableView(group_service=self._groups)
        self._table.deleteRequested.connect(self._on_delete)
        self._table.cellEdited.connect(self._on_cell_edited)
        self._table.entryEditRequested.connect(self._on_multi_code_edit)
        self._table.groupEdited.connect(self._on_group_edited)
        right.addWidget(self._table, 1)
        self._library_splitter.addWidget(right_widget)
        self._library_splitter.setStretchFactor(0, 0)
        self._library_splitter.setStretchFactor(1, 1)
        self._library_splitter.setSizes([210, 870])
        root.addWidget(self._library_splitter, 1)

        footer = QWidget(self)
        footer.setObjectName("PageActionBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 7, 8, 7)
        footer_layout.setSpacing(8)
        self._status = QLabel("")
        self._status.setObjectName("WorkspaceStatus")
        footer_layout.addWidget(self._status, 1)
        footer_layout.addWidget(self._btn_undo)
        footer_layout.addWidget(self._btn_save)
        root.addWidget(footer)

    # ------------------------------------------------------------------ #
    # 过滤 + 刷新（显隐模式：全量仅首次/数据变更时重建，切分组/搜索只显隐）
    # ------------------------------------------------------------------ #
    def refresh(self, force: bool = False) -> None:
        """Refresh only data or ordering that actually changed.

        Search and group switches call this method often.  Those paths must not
        rebuild signatures or sort the full dictionary; mutations explicitly
        request ``force=True`` instead.
        """
        sort_mode = self._sort_combo.currentText()
        need_rebuild = force or not self._all_phrases
        if need_rebuild:
            self._all_phrases = self._sort_phrases(self._repo.all())
            self._table.set_phrases(
                self._all_phrases,
                group_of=self._group_of,
                selected_of=lambda p: p.key in self._checked_keys(),
                code_display=self._display_store.display_for,
            )
            self._content_sig = self._content_signature(self._all_phrases)
            self._visibility_key = None
        elif sort_mode != self._last_sort_mode:
            self._all_phrases = self._sort_phrases(self._all_phrases)
            self._table.reorder(self._all_phrases)
            self._visibility_key = None
        self._last_sort_mode = sort_mode
        self._apply_visibility()
        self._update_status()

    def restyle(self) -> None:
        """主题切换后重设分组面板的动态主色（仅重设样式表，不重建对象）。

        『全部』按钮（_btn_all）主色是显式 setStyleSheet（值来自 accent_color() 即
        当前主题 @ACCENT@），QApplication.setStyleSheet 不会自动重算该控件，故需经
        GroupPanel.restyle() 主动重设。restyle 只改样式表，不丢表单数据、不重建窗口。
        """
        if hasattr(self, "_group_panel") and self._group_panel is not None:
            self._group_panel.restyle()

    def refresh_scheme_status(self) -> None:
        """Refresh persistent scheme/file context without rebuilding the table."""
        state = resolve_active_scheme(self._repo.path.parent)
        filename = self._repo.path.name or self.PHRASE_FILE
        summary = state.files.display_name if state.files else "方案未确认"
        self._scheme_summary.setText(
            f"当前方案：{summary}　｜　本页词库：{filename}　｜　{state.reason}"
        )
        self._scheme_summary.setToolTip(
            f"当前方案：{summary}\n本页词库：{filename}\n{state.reason}"
        )
        self._scheme_summary.setProperty("role", "info" if state.writable else "warning")
        self._scheme_summary.style().unpolish(self._scheme_summary)
        self._scheme_summary.style().polish(self._scheme_summary)

    def show_environment_warning(self, message: str) -> None:
        """Surface a global scheme/write guard without reaching into private UI."""
        self._status.setProperty("role", "warning")
        self._refresh_status_style()
        self._status.setText(message)

    def _apply_visibility(self) -> None:
        """根据当前分组与搜索关键字显隐行（不重建表格）。"""
        grp = self._current_group
        kw = self._keyword.strip().lower()
        visibility_key = (grp, kw, self._quick_filter, self._last_sort_mode)
        if visibility_key == self._visibility_key:
            return
        self._visibility_key = visibility_key
        if not kw and self._quick_filter == "全部" and grp == GroupService.all_group_label():
            self._table.apply_filter(None)
            self._displayed = list(self._all_phrases)
            return
        wanted = (
            set(self._groups.entries_of(grp))
            if grp != GroupService.all_group_label() else None
        )
        visible = []
        for p in self._all_phrases:
            if kw and kw not in p.text.lower() and kw not in p.code.lower():
                continue
            if self._quick_filter == "未分组" and self._group_of(p):
                continue
            if self._quick_filter == "英文" and not p.text.isascii():
                continue
            if self._quick_filter == "低权重" and p.weight > 5:
                continue
            if wanted is not None and p.key not in wanted:
                continue
            visible.append(p)
        self._table.apply_filter({p.key for p in visible})
        self._displayed = visible

    def _update_status(self) -> None:
        n = len(self._displayed)
        label = ("全部" if self._current_group == GroupService.all_group_label()
                 else self._current_group)
        txt = f"当前分组：{label}　共 {n} 条"
        if self._keyword:
            txt += f"（搜索：{self._keyword}）"
        self._status.setText(txt)

    def _checked_keys(self) -> set:
        """当前勾选集合（来自表格，作为权威来源）。"""
        return set(self._table.get_checked_keys())

    def _content_signature(self, phrases: List[Phrase]) -> Tuple:
        """内容签名（文本/编码/权重/分组），不含排序键；用于判断是否需重建 model。
        分组与搜索通过显隐实现，不影响全量内容，故不计入签名。
        """
        return tuple(
            (p.text, p.code, p.weight,
             (self._group_of(p) if self._group_of else ""))
            for p in phrases
        )

    def _sort_phrases(self, phrases: List[Phrase]) -> List[Phrase]:
        key_map = {"权重": "weight", "拼音": "code", "文本": "text",
                   "加入顺序倒序": "order"}
        key = key_map.get(self._sort_combo.currentText(), "weight")
        if key == "weight":
            return sorted(phrases, key=lambda item: item.weight, reverse=True)
        if key == "code":
            return sorted(phrases, key=lambda item: item.code)
        if key == "text":
            return sorted(phrases, key=lambda item: item.text)
        return list(phrases)[::-1]

    def _group_of(self, phrase: Phrase) -> str:
        return self._groups.get_entry_group(phrase.text, phrase.code)

    # ------------------------------------------------------------------ #
    # 内联编辑（双击文本/编码/权重）
    # ------------------------------------------------------------------ #
    def _on_cell_edited(self, row: int, col: int, value: str) -> None:
        if not (0 <= row < len(self._displayed)):
            return
        p = self._displayed[row]
        if col == COL_TEXT:
            old = p.text
            display = self._display_store.display_for(p)
            p.text = value
            self._groups.remap_entry_key(old, p.code, p.text, p.code, save=False)
            self._display_store.set(p.text, p.code, display)
        elif col == COL_CODE:
            old = p.code
            display = normalize_display_code(value)
            p.code = raw_code(display)
            self._groups.remap_entry_key(p.text, old, p.text, p.code, save=False)
            self._display_store.set(p.text, p.code, display)
        elif col == COL_WEIGHT:
            try:
                p.weight = self._clamp_weight(int(value))
            except ValueError:
                p.weight = self._clamp_weight(p.weight)
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._btn_save.setEnabled(True)
        self._btn_undo.setEnabled(True)
        self._status.setProperty("role", "warning")
        self._status.setText("● 有未保存的修改（点『保存』写盘并按设置部署）")
        self._refresh_status_style()

    def has_unsaved_changes(self) -> bool:
        """Return whether the workbench owns staged, not-yet-persisted edits."""
        return self._dirty

    def confirm_discard_changes(self, action: str) -> bool:
        """Guard operations that would replace or destroy the attached repository."""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "放弃未保存修改？",
            f"当前个人词库有尚未保存的修改。\n"
            f"继续{action}将放弃这些修改。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _clear_dirty(self) -> None:
        self._dirty = False
        self._btn_save.setEnabled(False)
        self._btn_undo.setEnabled(False)
        self._status.setProperty("role", "neutral")
        self._refresh_status_style()

    def _refresh_status_style(self) -> None:
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_group_state_changed(self, message: str) -> None:
        self._mark_dirty()
        self._status.setProperty("role", "warning")
        self._refresh_status_style()
        self._status.setText(message)
        self.refresh(force=True)

    def _backup_before_group_delete(self) -> tuple[bool, str]:
        filenames = (self._repo.path.name, self._groups.path.name, self._display_store.path.name)
        existing = [name for name in filenames if (self._repo.path.parent / name).is_file()]
        report = self._backup.backup_files_report(filenames)
        failed = [name for name in existing if report.saved.get(name) is None]
        if failed:
            return False, "以下文件未能完成备份：" + "、".join(failed)
        return True, f"已备份当前方案的 {len(existing)} 个词库相关文件。"

    def _on_save(self) -> None:
        if not self._dirty:
            return
        self._persist_backup()
        self._display_store.prune(self._repo.all())
        try:
            self._save_phrase_bundle()
        except Exception as exc:
            self._mark_dirty()
            self._status.setProperty("role", "error")
            self._status.setText(f"保存失败，未保存修改已保留：{exc}")
            self.editCompleted.emit(False, self._status.text())
            return
        self._clear_dirty()
        self._record_history("词库保存", "保存工作台中的暂存修改")
        self._status.setProperty("role", "success")
        self.refresh(force=True)
        self._schedule_deploy()
        self.editCompleted.emit(True, self._status.text())

    def _on_undo(self) -> None:
        if not self._dirty:
            return
        # 放弃内存改动：强制从磁盘与 sidecar 重载
        self._repo.load(force=True)
        self._groups.load(force=True)
        self._display_store.load()
        self._clear_dirty()
        self.refresh(force=True)
        self._status.setText("已撤销未保存的修改。")

    # ------------------------------------------------------------------ #
    # 自动分组
    # ------------------------------------------------------------------ #
    def _auto_group(self, only_ungrouped: bool = True) -> int:
        """按 ClassificationService 规则归类。返回调整的条目数。

        分组 sidecar 批量保存一次，避免逐条写盘拖慢启动/大批量操作。
        """
        changed = 0
        for p in self._repo.all():
            cur = self._groups.get_entry_group(p.text, p.code)
            if only_ungrouped and cur:
                continue
            target = ClassificationService.classify(p.text)
            if target != cur:
                self._groups.set_entry_group(p.text, p.code, target, save=False)
                changed += 1
        if changed:
            self._save_phrase_bundle(
                include_phrase=False, include_display=False,
            )
        return changed

    def _on_auto_group(self) -> None:
        """按钮：仅对未分组条目分组。"""
        try:
            self._ensure_write_target()
        except RuntimeError as exc:
            QMessageBox.warning(self, "方案已变化", str(exc))
            return
        changed = self._auto_group(only_ungrouped=True)
        if changed:
            self._status.setText(
                f"自动分组完成，调整 {changed} 条（分组 sidecar 已保存）。")
        else:
            self._status.setText("无未分组条目需要调整。")
        self._group_panel.refresh()
        self.refresh(force=True)

    def _maybe_first_run_auto_group(self) -> None:
        if self._settings.auto_group_done:
            return
        try:
            self._ensure_write_target()
        except RuntimeError:
            # Do not mutate an old in-memory repository during a scheme switch.
            return
        changed = self._auto_group(only_ungrouped=True)
        self._settings.auto_group_done = True
        if changed > 0:
            QTimer.singleShot(200, lambda: self._show_auto_group_popup(changed))

    def _show_auto_group_popup(self, count: int) -> None:
        """首次自动分组完成提示：3 秒后自动关闭；可点关闭按钮 / 回车关闭。"""
        # 无窗口测试平台不创建延时模态框，避免页面销毁后的回调访问失效对象。
        if QApplication.platformName() == "offscreen":
            return
        dlg = QMessageBox(
            QMessageBox.Icon.Information, "自动分组完成",
            f"首次启动已自动归类 {count} 条词库（英文 / 符号数字 / 单字 / 地名 / 人名 / 其他）。\n\n"
            "可随时点『自动分组』按钮仅对未分组条目重新归类。",
            QMessageBox.StandardButton.Ok, self,
        )
        QTimer.singleShot(3000, dlg.accept)  # 3 秒后自动关闭
        dlg.exec()

    # ------------------------------------------------------------------ #
    # 分组下拉提交（双击分组列）
    # ------------------------------------------------------------------ #
    def _on_group_edited(self, row: int, group: str) -> None:
        if not (0 <= row < len(self._displayed)):
            return
        p = self._displayed[row]
        self._groups.set_entry_group(p.text, p.code, group, save=False)
        self._table.update_group_cell(row, group)
        self._group_panel.refresh()
        self._mark_dirty()

    # ------------------------------------------------------------------ #
    # 选择 / 批量删除
    # ------------------------------------------------------------------ #
    def _on_select_all(self) -> None:
        self._all_selected = not self._all_selected
        self._table.set_all_checked(self._all_selected)
        self._btn_select_all.setText("取消全选" if self._all_selected else "全选")
        n = len(self._checked_keys())
        self._status.setText(f"已选择 {n} 条。" if n else "共 0 条。")

    def _on_toggle_batch_select(self) -> None:
        """批量选择：开启后显示选择列 + 全选/批量删除；再次点击隐藏并清空勾选。"""
        self._batch_mode = not self._batch_mode
        self._table.set_select_column_visible(self._batch_mode)
        self._btn_select_all.setVisible(self._batch_mode)
        self._btn_batch_del.setVisible(self._batch_mode)
        self._btn_batch_group.setVisible(self._batch_mode)
        self._batch_bar.setVisible(self._batch_mode)
        self._btn_batch_select.setText("退出批量" if self._batch_mode else "批量选择")
        if not self._batch_mode:
            self._table.set_all_checked(False)
            self._all_selected = False
            self._btn_select_all.setText("全选")
            self._status.setText("已退出批量选择。")

    def _on_batch_group(self) -> None:
        keys = self._checked_keys()
        if not keys:
            self._status.setText("未选择任何条目。")
            return
        options = ["未分组"] + self._groups.list_groups()
        group, ok = QInputDialog.getItem(self, "批量调整分组", "分组：", options, 0, False)
        if not ok:
            return
        target = "" if group == "未分组" else group
        self._persist_backup()
        for key in keys:
            text, _, code = key.partition("\t")
            self._groups.set_entry_group(text, code, target, save=False)
        self._save_phrase_bundle(
            include_phrase=False, include_display=False,
        )
        self._record_history("批量调整分组", f"调整为「{group}」", keys)
        self._exit_batch_mode()
        self._group_panel.refresh()
        self.refresh(force=True)
        self._status.setProperty("role", "success")
        self._status.setText(f"已将 {len(keys)} 条调整为「{group}」。")
        self.editCompleted.emit(True, self._status.text())

    def _on_batch_delete(self) -> None:
        keys = self._checked_keys()
        if not keys:
            self._status.setText("未勾选任何条目。")
            return
        if not confirm_delete(
            self,
            "批量删除",
            f"确认删除选中的 {len(keys)} 条？此操作不可撤销。",
        ):
            return
        self._persist_backup()
        for key in keys:
            text, _, code = key.partition("\t")
            self._repo.delete(text, code)
            self._groups.set_entry_group(text, code, "", save=False)
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._metadata.prune(self._repo.all())
        self._record_history("批量删除", f"删除 {len(keys)} 条", keys)
        self._schedule_deploy()
        self._exit_batch_mode()
        self.refresh(force=True)
        self._status.setText(f"已删除 {len(keys)} 条。")

    # ------------------------------------------------------------------ #
    # 单条删除（保留确认）
    # ------------------------------------------------------------------ #
    def _on_delete(self, phrase: Phrase) -> None:
        siblings = [item for item in self._repo.all() if item.text == phrase.text]
        if len(siblings) == 1:
            selected = siblings if confirm_delete(
                self, "删除词条", f"确认删除「{phrase.text} / {phrase.code}」？"
            ) else []
        else:
            dialog = CodeDeleteDialog(phrase.text, siblings, phrase.code, self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            selected = dialog.selected()
        if not selected:
            return
        self._persist_backup()
        for item in selected:
            self._repo.delete(item.text, item.code)
            self._groups.set_entry_group(item.text, item.code, "", save=False)
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._record_history(
            "删除词条", f"删除「{phrase.text}」的 {len(selected)} 个编码",
            [item.key for item in selected],
        )
        self._schedule_deploy()
        self.refresh(force=True)
        codes = "、".join(item.code for item in selected)
        message = f"已删除「{phrase.text}」的 {len(selected)} 个编码：{codes}。"
        self._status.setProperty("role", "success")
        self._status.setText(message)
        self.editCompleted.emit(True, message)

    # ------------------------------------------------------------------ #
    # 信号处理
    # ------------------------------------------------------------------ #
    def _on_search(self, text: str) -> None:
        self._keyword = text
        self.refresh()

    def _on_group_selected(self, name: str) -> None:
        self._current_group = name
        self.refresh()

    def _on_sort_changed(self, _text: str) -> None:
        self.refresh()


    def _on_filter_changed(self, text: str) -> None:
        self._quick_filter = text
        self.refresh()

    def _saved_filter_bucket(self) -> tuple[dict, str, dict]:
        all_filters = self._settings.get("saved_library_filters", {})
        all_filters = dict(all_filters) if isinstance(all_filters, dict) else {}
        resolution = resolve_active_scheme(self._repo.path.parent)
        schema_id = str(resolution.schema_id or self._repo.path.name)
        bucket = all_filters.get(schema_id, {})
        bucket = dict(bucket) if isinstance(bucket, dict) else {}
        return all_filters, schema_id, bucket

    def _rebuild_saved_filters_menu(self) -> None:
        menu = self._saved_filters_menu
        menu.clear()
        save_action = menu.addAction("保存当前筛选…")
        save_action.triggered.connect(self._save_current_filter)
        _all, _schema, bucket = self._saved_filter_bucket()
        if not bucket:
            empty = menu.addAction("尚未保存筛选")
            empty.setEnabled(False)
            return
        menu.addSeparator()
        for name, state in bucket.items():
            action = menu.addAction(str(name))
            action.triggered.connect(
                lambda _=False, selected=dict(state): self._apply_saved_filter(selected)
            )
        menu.addSeparator()
        delete_menu = menu.addMenu("删除已保存筛选")
        for name in bucket:
            action = delete_menu.addAction(str(name))
            action.triggered.connect(
                lambda _=False, selected=str(name): self._delete_saved_filter(selected)
            )

    def _save_current_filter(self) -> None:
        name, ok = QInputDialog.getText(self, "保存常用筛选", "名称：")
        name = name.strip()
        if not ok or not name:
            return
        all_filters, schema_id, bucket = self._saved_filter_bucket()
        bucket[name] = {
            "keyword": self._keyword,
            "group": self._current_group,
            "filter": self._quick_filter,
            "sort": self._sort_combo.currentText(),
        }
        all_filters[schema_id] = bucket
        self._settings.set("saved_library_filters", all_filters)
        self._status.setText(f"已保存常用筛选「{name}」。")

    def _delete_saved_filter(self, name: str) -> None:
        all_filters, schema_id, bucket = self._saved_filter_bucket()
        bucket.pop(name, None)
        all_filters[schema_id] = bucket
        self._settings.set("saved_library_filters", all_filters)
        self._status.setText(f"已删除常用筛选「{name}」。")

    def _apply_saved_filter(self, state: dict) -> None:
        keyword = str(state.get("keyword", ""))
        group = str(state.get("group", GroupService.all_group_label()))
        quick_filter = str(state.get("filter", "全部"))
        sort = str(state.get("sort", "加入顺序倒序"))
        self._search.blockSignals(True)
        self._filter_combo.blockSignals(True)
        self._sort_combo.blockSignals(True)
        try:
            self._search.setText(keyword)
            if self._filter_combo.findText(quick_filter) >= 0:
                self._filter_combo.setCurrentText(quick_filter)
            if self._sort_combo.findText(sort) >= 0:
                self._sort_combo.setCurrentText(sort)
        finally:
            self._search.blockSignals(False)
            self._filter_combo.blockSignals(False)
            self._sort_combo.blockSignals(False)
        self._keyword = keyword
        self._quick_filter = self._filter_combo.currentText()
        self._group_panel.select(group, emit=False)
        self._current_group = self._group_panel.current_group()
        self.refresh()

    def _group_counts(self) -> dict[str, int]:
        # Reuse the table's full-list cache. Group refreshes are frequent after
        # edits; asking the repository for another copied list is unnecessary.
        phrases = self._all_phrases if self._all_phrases else self._repo.all()
        counts: dict[str, int] = {name: 0 for name in self._groups.list_groups()}
        counts[GroupService.all_group_label()] = len(phrases)
        for phrase in phrases:
            group = self._group_of(phrase)
            if group:
                counts[group] = counts.get(group, 0) + 1
        return counts

    def _exit_batch_mode(self) -> None:
        if self._batch_mode:
            self._batch_mode = False
            self._table.set_select_column_visible(False)
            self._btn_select_all.setVisible(False)
            self._btn_batch_del.setVisible(False)
            self._btn_batch_group.setVisible(False)
            self._batch_bar.hide()
            self._btn_batch_select.setText("批量选择")
        self._table.set_all_checked(False)
        self._all_selected = False
        self._btn_select_all.setText("全选")

    def _open_duplicates(self) -> None:
        DuplicateManagerDialog(
            self._repo.all(), self._apply_duplicate_group, self,
            self._display_store.editor_display_for,
            code_alphabet=self._code_alphabet,
        ).exec()

    def _apply_duplicate_group(self, original: list[Phrase], entries: list[Phrase]) -> None:
        if not entries:
            QMessageBox.warning(self, "未保存", "至少保留一条有效编码。")
            return
        self._persist_backup()
        preserved_groups = {(item.text, item.code): self._group_of(item) for item in original}
        for item in original:
            self._repo.delete(item.text, item.code)
            self._groups.set_entry_group(item.text, item.code, "", save=False)
        for item in entries:
            code = raw_code(item.code)
            self._repo.upsert(item.text, code, self._clamp_weight(item.weight))
            self._groups.set_entry_group(item.text, code, preserved_groups.get((item.text, item.code), ""), save=False)
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._metadata.prune(self._repo.all())
        before = "；".join(f"{item.text}/{self._display_store.display_for(item)}@{item.weight}" for item in original)
        after = "；".join(f"{item.text}/{item.code}@{item.weight}" for item in entries)
        final_keys = {(item.text, raw_code(item.code), item.weight) for item in entries}
        removed = [item for item in original if (item.text, raw_code(item.code), item.weight) not in final_keys]
        removed_text = "；".join(f"{item.text}/{self._display_store.display_for(item)}" for item in removed)
        detail = f"修改前：{before or '无'}；修改后：{after or '无'}"
        if removed_text:
            detail += f"；删除：{removed_text}"
        self._record_history("重码集中编辑", detail, [item.key for item in original])
        self.refresh(force=True)
        self._group_panel.refresh()
        self._schedule_deploy()
        message = f"已集中保存 {len(entries)} 条重码项目。"
        self._status.setProperty("role", "success")
        self._status.setText(message)
        self.editCompleted.emit(True, message)

    def _health_result_phrases(self, text: str, code: str) -> list[Phrase]:
        normalized = raw_code(code)
        exact = [
            item for item in self._repo.all()
            if item.text == text and raw_code(item.code) == normalized
        ]
        if exact:
            return exact
        keyword = text or normalized
        return self._repo.search(keyword) if keyword else []

    def _open_health_result(self, text: str, code: str) -> None:
        label = text or code or "空条目"
        PhraseSearchResultDialog(
            f"查看词条：{label}",
            lambda: self._health_result_phrases(text, code),
            self._on_multi_code_edit,
            self._on_delete,
            self._groups,
            self,
            self._display_store.editor_display_for,
        ).exec()

    def open_health_check(self) -> None:
        phrases = self._repo.all()
        LibraryHealthDialog(
            phrases, health_check(phrases, self._groups),
            self._open_health_result, self,
            self._display_store.editor_display_for,
        ).exec()

    def _open_history(self) -> None:
        HistoryDialog(
            self._metadata.history(), self._rollback_history, self,
        ).exec()

    def _rollback_history(self, item: dict) -> bool:
        if self._dirty:
            QMessageBox.warning(self, "存在未保存修改", "请先保存或撤销当前修改，再执行历史回滚。")
            return False
        backups = item.get("backups")
        if not isinstance(backups, dict) or not backups:
            return False
        try:
            self._ensure_write_target()
            contents: dict[str, bytes] = {}
            for filename, value in backups.items():
                source = Path(str(value))
                known = {path.resolve() for path in self._backup.list_backups(str(filename))}
                if not source.is_file() or source.resolve() not in known:
                    raise RuntimeError(f"备份已被轮转或移动：{filename}")
                contents[str(filename)] = source.read_bytes()
            answer = QMessageBox.question(
                self, "回滚词库历史",
                f"将恢复到“{item.get('action', '所选操作')}”发生前的状态。"
                "\n当前版本会先备份，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self._persist_backup()
            writer = self._transaction_writer or TransactionalFileWriter(
                self._repo.path.parent / ".rime-config-transactions"
            )
            changes = [
                FileChange(
                    self._repo.path.parent / filename, data,
                    capture_revision(self._repo.path.parent / filename),
                )
                for filename, data in contents.items()
            ]
            writer.commit(changes)
            self._repo.load(force=True)
            self._groups.load(force=True)
            self._display_store.load()
            self._record_history(
                "历史回滚", f"已恢复到 {item.get('at', '')} 前的状态",
            )
            self.refresh(force=True)
            self._group_panel.refresh()
            self._schedule_deploy()
            self._status.setText("历史版本已恢复，并已保留回滚前的备份。")
            return True
        except Exception as exc:
            QMessageBox.warning(self, "无法回滚", str(exc))
            return False

    def _open_scheme_import(self) -> None:
        if self._dirty:
            QMessageBox.warning(
                self,
                "存在未保存修改",
                "请先保存或撤销当前词库修改，再创建跨方案导入预览。",
            )
            return
        resolution = resolve_active_scheme(self._repo.path.parent)
        if not resolution.writable or resolution.files is None:
            QMessageBox.warning(
                self, "无法确认导入目标",
                "请先在 Rime 中切换到要接收词条的输入方案，再重新打开跨方案导入。",
            )
            return
        target = resolution.files
        sources = [item for item in known_managed_schemes() if item.schema_id != target.schema_id]
        if not sources:
            QMessageBox.warning(self, "没有可用来源", "未找到另一个已适配的个人词库。")
            return
        source = sources[0]
        if not (self._repo.path.parent / source.phrase_filename).is_file():
            QMessageBox.warning(
                self, "没有可用来源",
                f"未找到 {source.display_name} 个人词库：{source.phrase_filename}",
            )
            return
        if target.schema_id == "wanxiang" and source.schema_id == "rime_frost":
            service = WanxiangImportService(
                self._repo.path.parent, self._backup.backup_dir, self._deploy.deploy,
            )
        else:
            service = DirectSchemeImportService(
                self._repo.path.parent,
                self._backup.backup_dir,
                self._deploy.deploy,
                source_filename=source.phrase_filename,
                target_filename=target.phrase_filename,
                source_name=source.display_name,
                target_name=target.display_name,
            )
        dialog = WanxiangImportDialog(
            service,
            sandbox_mode=bool(getattr(self._settings, "sandbox_mode", False)),
            parent=self,
        )
        dialog.importCompleted.connect(self._on_scheme_import_completed)
        dialog.exec()

    def _on_scheme_import_completed(self, message: str) -> None:
        self.refresh_scheme_status()
        self._status.setText(message)

    def _selected_phrase(self) -> Phrase | None:
        index = self._table.currentIndex()
        if index.isValid() and 0 <= index.row() < len(self._displayed):
            return self._displayed[index.row()]
        return None

    def open_dictionary_center(self, scope: str = "all") -> None:
        if self._dirty:
            QMessageBox.warning(
                self,
                "存在未保存修改",
                "请先保存或撤销当前个人词库修改，再打开词典中心。",
            )
            return
        dialog = self.create_dictionary_center(scope=scope, parent=self)
        dialog.exec()

    def create_dictionary_center(
            self, scope: str = "all", parent=None,
            embedded: bool = False) -> DictionaryCenterDialog:
        """Build a dictionary center for either the navigation page or a modal caller."""
        return DictionaryCenterDialog(
            self._repo.path.parent,
            tune_entry=self._open_dictionary_entry_tune,
            scope=scope,
            parent=self if parent is None else parent,
            data_engine_client=self._data_engine_client,
            dictionary_index=self._system_dictionary_index,
            transaction_writer=self._transaction_writer,
            backup_service=self._backup,
            saved_callback=self._on_dictionary_file_saved,
            embedded=embedded,
        )

    def _on_dictionary_file_saved(self, path: str) -> None:
        self._metadata.record("词典编辑", str(path))
        self._metadata.save()
        self._schedule_deploy()

    def _open_dictionary_entry_tune(self, text: str, code: str) -> None:
        if self._dirty:
            QMessageBox.warning(
                self,
                "存在未保存修改",
                "请先保存或撤销当前修改，再调节词典中的词条。",
            )
            return
        dialog = QuickTuneDialog(
            self._repo,
            self._pinyin,
            self._rime_preview_service,
            self._save_quick_tune_changes,
            parent=self,
            dictionary_index=self._system_dictionary_index,
        )
        dialog.select_candidate(text, code)
        dialog.exec()
        dialog.dispose()

    def _on_quick_tune(self) -> None:
        if self._dirty:
            QMessageBox.warning(
                self,
                "存在未保存修改",
                "请先保存或撤销当前修改，再打开字词快速调节。",
            )
            return
        phrase = self._selected_phrase()
        dialog = QuickTuneDialog(
            self._repo,
            self._pinyin,
            self._rime_preview_service,
            self._save_quick_tune_changes,
            phrase,
            self,
            dictionary_index=self._system_dictionary_index,
        )
        dialog.exec()
        dialog.dispose()

    def _save_quick_tune_changes(
            self, entries: list[Phrase],
            expected_signature: tuple[int, int] | None) -> tuple[bool, str]:
        try:
            stat = self._repo.path.stat()
            current_signature = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            current_signature = None
        if current_signature != expected_signature:
            return False, "个人词库已被其他程序修改；本次未写入，请关闭窗口后重新打开。"
        original_entries = self._repo.all()
        self._persist_backup()
        try:
            self._repo.replace_all(entries)
            self._save_phrase_bundle(
                include_groups=False, include_display=False,
            )
        except Exception:
            self._repo.replace_all(original_entries)
            raise
        self.refresh(force=True)
        self._record_history("快速调节", "保存字词候选顺序调整")
        self._schedule_deploy()
        message = "快速调节的全部修改已统一保存，并按设置处理部署。"
        self._status.setProperty("role", "success")
        self._status.setText(message)
        self.editCompleted.emit(True, message)
        return True, message


    def _on_add(self) -> None:
        original_groups = set(self._groups.list_groups())
        editor = PhraseEditor(
            phrase=None, groups=self._groups.list_groups(),
            pinyin=self._pinyin, repo=self._repo,
            system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._create_group_staged,
            code_alphabet=self._code_alphabet, parent=self,
        )
        if self._current_group != GroupService.all_group_label():
            editor.set_group_hint(self._current_group)
        if editor.exec() == editor.DialogCode.Accepted:
            vals = editor.get_values()
            try:
                self._apply_multiple_codes(vals)
                codes = [raw_code(str(vals.get("code", "")))] + [raw_code(code) for code in vals.get("additional_codes", [])]
                message = f"已保存「{vals['text']}」：" + "、".join(dict.fromkeys(code for code in codes if code)) + "。"
                self._status.setProperty("role", "success")
                self._status.setText(message)
                self.editCompleted.emit(True, message)
            except Exception as exc:
                message = f"保存失败：{exc}"
                self._status.setProperty("role", "error")
                self._status.setText(message)
                self.editCompleted.emit(False, message)
        else:
            self._discard_staged_groups(original_groups)

    def _on_multi_code_edit(self, phrase: Phrase) -> None:
        phrases = [item for item in self._repo.all() if item.text == phrase.text]
        original_groups = set(self._groups.list_groups())
        editor = MultiCodeEditor(
            phrase.text, phrases, self._repo, self._pinyin,
            self._display_store.display_for, self,
            groups=self._groups.list_groups(),
            group=self._groups.get_entry_group(phrase.text, phrase.code),
            system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._create_group_staged,
            code_alphabet=self._code_alphabet,
        )
        if editor.exec() != editor.DialogCode.Accepted:
            self._discard_staged_groups(original_groups)
            return
        entries = editor.entries()
        updated_text = editor.text_value()
        try:
            group = editor.group_value()
            self._persist_backup()
            for old in phrases:
                self._repo.delete(old.text, old.code)
                self._groups.set_entry_group(old.text, old.code, "", save=False)
            for entry in entries:
                code = str(entry["code"])
                weight = self._clamp_weight(int(entry["weight"]))
                self._repo.upsert(updated_text, code, weight)
                self._groups.set_entry_group(updated_text, code, group, save=False)
                self._display_store.set(updated_text, code, str(entry["display_code"]))
            self._display_store.prune(self._repo.all())
            self._save_phrase_bundle()
            self._record_history(
                "编辑词条", f"保存「{updated_text}」的 {len(entries)} 个编码",
            )
            self._status.setProperty("role", "success")
            message = f"已保存「{updated_text}」的 {len(entries)} 个编码：" + "、".join(str(entry["code"]) for entry in entries) + "。"
            self._status.setText(message)
            self.refresh(force=True)
            self._schedule_deploy()
            self.editCompleted.emit(True, message)
        except Exception as exc:
            message = f"保存多个编码失败：{exc}"
            self._status.setProperty("role", "error")
            self._status.setText(message)
            self.editCompleted.emit(False, message)

    def _apply_multiple_codes(self, values: dict) -> Phrase:
        text = str(values["text"])
        group = str(values["group"])
        group_explicit = bool(values.get("group_explicit", False))
        weight = self._clamp_weight(int(values["weight"]))
        weight_explicit = bool(values.get("weight_explicit", False))
        weight_updates = {
            str(key): self._clamp_weight(int(new_weight))
            for key, new_weight in values.get("weight_updates", {}).items()
        }
        displays = [str(values.get("display_code", ""))] + list(values.get("additional_codes", []))
        unique: list[str] = []
        for display in displays:
            display = validate_code_input(
                display, self._code_alphabet, allow_empty=True,
            )
            if raw_code(display) and raw_code(display) not in {raw_code(item) for item in unique}:
                unique.append(display)
        if not unique:
            unique = [""]
        self._persist_backup()
        for key, new_weight in weight_updates.items():
            old_text, separator, old_code = key.partition("\t")
            if separator:
                self._repo.update_weight(old_text, old_code, new_weight)
        phrase = None
        for display in unique:
            code = raw_code(display)
            key = f"{text}\t{code}"
            existing = self._repo.find(text, code)
            requested_weight = weight
            if existing is not None and key in weight_updates:
                requested_weight = None
            elif existing is not None and not weight_explicit:
                requested_weight = None
            phrase, _new, _conflict = self._repo.upsert(
                text, code, requested_weight
            )
            if existing is None or group_explicit:
                self._groups.set_entry_group(text, code, group, save=False)
            self._display_store.set(text, code, display)
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._record_history("新增或更新词条", f"保存「{text}」的 {len(unique)} 个编码")
        self._reset_to_latest_view()
        self.refresh(force=True)
        if phrase is None:
            raise RuntimeError("未能保存词条")
        self._table.select_key(phrase.key)
        self._schedule_deploy()
        return phrase

    def _apply_upsert(self, text: str, code: str, weight: int,
                      group: str, is_new: bool,
                      old: Optional[Phrase] = None,
                      reset_view: bool = False, display_code: str = "",
                      weight_updates: Optional[dict[str, int]] = None) -> None:
        weight = self._clamp_weight(weight)
        code = raw_code(code)
        code = raw_code(validate_code_input(
            code, self._code_alphabet, allow_empty=True,
        ))
        existing = self._repo.find(text, code)
        for key, new_weight in (weight_updates or {}).items():
            existing_text, separator, existing_code = key.partition("\t")
            if separator:
                self._repo.update_weight(
                    existing_text, existing_code, self._clamp_weight(new_weight))
        if existing is not None:
            weight = existing.weight
        self._persist_backup()
        phrase, _new, conflict = self._repo.upsert(text, code, weight)
        self._groups.set_entry_group(text, code, group, save=False)
        self._display_store.set(text, code, display_code)
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._record_history("编辑词条", f"保存「{text} / {code}」")
        if conflict:
            QMessageBox.information(
                self, "已合并",
                f"「{text} / {code}」已存在，已按当前权重更新原词条。"
            )
        if reset_view:
            self._reset_to_latest_view()
        self.refresh(force=True)
        if reset_view:
            self._table.select_key(phrase.key)
        self._schedule_deploy()

    # ------------------------------------------------------------------ #
    def quick_add(self, text: str, code: str = "", weight: int = 1,
                  group: str = "") -> None:
        """供热键收藏调用：直接入库（不经弹窗二次确认）。"""
        weight = self._clamp_weight(weight)
        self._persist_backup()
        code = raw_code(code)
        phrase, _new, _conflict = self._repo.upsert(text, code, weight)
        if text and code:
            self._groups.set_entry_group(text, code, group, save=False)
        self._display_store.set(text, code, normalize_display_code(code))
        self._display_store.prune(self._repo.all())
        self._save_phrase_bundle()
        self._record_history("快速收藏", f"保存「{text} / {code}」")
        self._reset_to_latest_view()
        self.refresh(force=True)
        self._table.select_key(phrase.key)
        self._schedule_deploy()

    # ------------------------------------------------------------------ #
    def _reset_to_latest_view(self) -> None:
        self._keyword = ""
        if self._search.text():
            self._search.blockSignals(True)
            self._search.clear()
            self._search.blockSignals(False)
        if self._sort_combo.currentText() != "加入顺序倒序":
            self._sort_combo.blockSignals(True)
            self._sort_combo.setCurrentText("加入顺序倒序")
            self._sort_combo.blockSignals(False)
        self._current_group = GroupService.all_group_label()
        self._group_panel.select_all(emit=False)

    @classmethod
    def _clamp_weight(cls, weight: int) -> int:
        return max(cls.MIN_WEIGHT, min(cls.MAX_WEIGHT, int(weight)))

    # ------------------------------------------------------------------ #
    def _save_phrase_bundle(
        self,
        *,
        include_phrase: bool = True,
        include_groups: bool = True,
        include_display: bool = True,
    ) -> None:
        """Persist related user files as one recoverable transaction."""
        self._ensure_write_target()
        try:
            self._bundle_writer.save(
                self._repo,
                self._groups,
                self._display_store,
                include_phrase=include_phrase,
                include_groups=include_groups,
                include_display=include_display,
            )
        except Exception:
            # The writer has already restored every target file.  Keep the
            # in-memory edits so the user can retry or explicitly undo them.
            self._mark_dirty()
            raise

    def _persist_backup(self) -> None:
        self._ensure_write_target()
        self._last_backup_files = self._bundle_writer.backup(
            self._repo, self._groups, self._display_store,
        )

    def _record_history(
            self, action: str, detail: str, entries=()) -> None:
        self._metadata.record(
            action, detail, entries, backups=self._last_backup_files,
        )
        self._metadata.save()
        self._last_backup_files = {}

    def _ensure_write_target(self) -> None:
        """Fail closed when Rime switched schemes after this page was attached."""
        resolution = resolve_active_scheme(self._repo.path.parent)
        expected = resolution.files.phrase_filename if resolution.files else ""
        if not resolution.writable:
            raise RuntimeError(f"当前输入方案无法安全确认：{resolution.reason}")
        if self._repo.path.name.casefold() != expected.casefold():
            raise RuntimeError(
                "检测到输入方案已经变化；当前页面仍连接旧词库，未写入任何文件。"
                "请等待页面自动切换后重试。"
            )

    def reattach(self, repo: PhraseRepo, groups: GroupService,
                 backup: BackupService,
                 system_dictionary_index: SystemDictionaryIndex | None = None,
                 rime_preview_service=None, metadata_store: MetadataStore | None = None,
                 code_alphabet: str = "") -> None:
        """Reconnect sidecars and views after Rime directory or sandbox changes."""
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._bundle_writer.backup_service = backup
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._metadata = metadata_store or MetadataStore(repo.path.parent)
        self._code_alphabet = code_alphabet
        self._display_store = PinyinDisplayStore(repo.path.parent, self._pinyin)
        self._group_panel.reattach(groups)
        self._group_panel.select_all(emit=False)
        self._group_panel.refresh()
        self._table._groups = groups
        self._table._model._groups = groups
        self._current_group = GroupService.all_group_label()
        self._keyword = ""
        self._content_sig = None
        self._last_sort_mode = None
        self._visibility_key = None
        self._clear_dirty()
        self.refresh_scheme_status()
        self.refresh(force=True)

    def dispose(self) -> None:
        """Stop UI polling before the owning main window is destroyed."""
        self._auto_deploy_timer.stop()
        self._deploy_poll.stop()

    def _schedule_deploy(self) -> None:
        """将自动部署移出保存和刷新主路径，避免阻塞界面。"""
        if self._settings.sandbox_mode:
            if self._rime_preview_service is not None:
                self._rime_preview_service.mark_waiting_for_deploy()
            self._status.setText("已保存到沙盒副本。")
            return
        if not self._settings.auto_deploy:
            if self._rime_preview_service is not None:
                self._rime_preview_service.mark_waiting_for_deploy()
            self._status.setText("已保存。未开启自动部署。")
            return
        if self._rime_preview_service is not None:
            self._rime_preview_service.mark_waiting_for_deploy()
        self._auto_deploy_timer.start()
        self._status.setText("已保存。正在合并连续修改，随后自动部署…")

    def _start_auto_deploy(self) -> None:
        """Deploy once after a short quiet period shared by consecutive saves."""
        if not self._deploy_coordinator.request():
            self._status.setText("已保存。部署任务正在进行。")
            self._deploy_poll.start()
            return
        self._status.setText("已保存。正在后台部署…")
        self._deploy_poll.start()

    def request_manual_deploy(self, *, rerun_if_running: bool = False) -> bool:
        """Queue a tray-requested deploy on the same serialized worker."""
        self._auto_deploy_timer.stop()
        if self._settings.sandbox_mode:
            self.manualDeploymentCompleted.emit(False, "沙盒模式，未触发真实部署。")
            return False
        if not self._deploy.available:
            self.manualDeploymentCompleted.emit(
                False, "未找到 WeaselDeployer.exe，请右键小狼毫托盘 → 重新部署",
            )
            return False
        self._manual_deploy_requested = True
        started = self._deploy_coordinator.request(
            rerun_if_running=rerun_if_running,
        )
        self._status.setText(
            "正在后台重新部署…" if started else "部署任务已在进行，请等待完成。"
        )
        self._deploy_poll.start()
        return True

    def request_auto_deploy(self) -> bool:
        """Queue deployment for another page on the shared serialized worker."""
        if self._settings.sandbox_mode or not self._settings.auto_deploy:
            return False
        if self._rime_preview_service is not None:
            self._rime_preview_service.mark_waiting_for_deploy()
        self._auto_deploy_timer.start()
        self._status.setText("正在合并连续修改，随后自动部署…")
        return True

    def hide_legacy_tools(self) -> None:
        """Hide low-frequency actions after they have moved to maintenance."""
        self._btn_tools.hide()

    def open_duplicates(self) -> None:
        self._open_duplicates()

    def run_auto_group(self) -> None:
        self._on_auto_group()

    def open_scheme_import(self) -> None:
        self._open_scheme_import()

    def open_history(self) -> None:
        self._open_history()

    def open_deployment_status(self) -> None:
        DeploymentStatusDialog(
            lambda: {
                "running": self._deploy_coordinator.running,
                "pending": self._deploy_coordinator.pending,
                "available": self._deploy.available,
            },
            self._metadata.history,
            self.request_manual_deploy,
            self,
        ).exec()

    def refresh_visuals(self) -> None:
        self.restyle()
        self._table.model().layoutChanged.emit()

    def _drain_deploy_results(self) -> None:
        """Apply deploy outcomes in the GUI thread."""
        outcome = self._deploy_coordinator.take_result()
        if outcome is None:
            return
        ok, msg = outcome.ok, outcome.message
        if outcome.rerun_requested:
            # A save landed while the previous deployment was running.  The
            # first result is not final: wait for Weasel to become stable, then
            # deploy the newest files once, without exposing an intermediate
            # success state to the preview service or the tray.
            self._status.setText("前次部署已结束，正在部署最新改动…")
            self._deploy_coordinator.request()
            self._deploy_poll.start()
            return

        self._deploy_poll.stop()
        manual = self._manual_deploy_requested
        if manual:
            self._status.setText("重新部署完成。" if ok else f"重新部署提示：{msg}")
        else:
            self._status.setText(
                "已保存并完成自动部署。" if ok else f"已保存。部署提示：{msg}"
            )
        self._metadata.record("手动部署" if manual else "自动部署", "成功" if ok else msg)
        self._metadata.save()
        if self._rime_preview_service is not None:
            if ok:
                self._rime_preview_service.invalidate_after_deploy()
                self.refresh_scheme_status()
            else:
                self._rime_preview_service.mark_waiting_for_deploy()
        if manual:
            self._manual_deploy_requested = False
            self.manualDeploymentCompleted.emit(ok, msg)

    # 供外部（主窗口热键）触发弹窗式快速收藏
    def open_quick_add(self, prefill_text: str = "", notice: str = "") -> None:
        from time import perf_counter
        from src.ui.quick_add_dialog import QuickAddDialog
        from src.utils.logger import get_logger

        started = perf_counter()
        original_groups = set(self._groups.list_groups())
        dlg = QuickAddDialog(
            prefill_text=prefill_text, groups=self._groups.list_groups(),
            pinyin=self._pinyin, repo=self._repo,
            system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._create_group_staged, notice=notice,
            code_alphabet=self._code_alphabet,
            parent=None,
        )
        get_logger(__name__).info(
            "快速收藏：窗口构建 elapsed_ms=%.1f",
            (perf_counter() - started) * 1000,
        )
        try:
            if dlg.exec() != dlg.DialogCode.Accepted:
                self._discard_staged_groups(original_groups)
                return
            try:
                vals = dlg.get_values()
                self._apply_multiple_codes(vals)
                codes = [vals.get("code", "")] + list(vals.get("additional_codes", []))
                unique_codes = []
                for code in codes:
                    code = raw_code(str(code))
                    if code and code not in unique_codes:
                        unique_codes.append(code)
                message = f"已收藏「{vals['text']}」：" + "、".join(unique_codes) + "。"
                self._status.setProperty("role", "success")
                self._status.setText(message)
                self.favoriteCompleted.emit(True, message)
            except Exception as exc:
                message = f"收藏失败：{exc}"
                self._status.setProperty("role", "error")
                self._status.setText(message)
                self.favoriteCompleted.emit(False, message)
        finally:
            dlg.dispose()
            dlg.deleteLater()

    def _create_group_staged(self, name: str) -> bool:
        return self._groups.add_group(name, save=False)

    def _discard_staged_groups(self, original: set[str]) -> None:
        for name in tuple(self._groups.list_groups()):
            if name not in original:
                self._groups.remove_group(name, save=False)
        self._group_panel.refresh()
