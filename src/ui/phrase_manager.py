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

import queue
import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QSizePolicy,
    QSplitter,
    QWidget,
)

from src.encoding.code_suggestions import normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.backup_service import BackupService
from src.service.classification_service import ClassificationService
from src.service.deploy_service import DeployService
from src.service.group_service import GroupService
from src.service.library_tools import MetadataStore, health_check
from src.service.pinyin_service import PinyinService
from src.service.pinyin_display_store import DISPLAY_INI_FILENAME, PinyinDisplayStore
from src.service.system_dictionary_index import SystemDictionaryIndex
from src.settings import Settings
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.delete_confirm_dialog import confirm_delete
from src.ui.code_delete_dialog import CodeDeleteDialog
from src.ui.group_panel import GroupPanel
from src.ui.library_dialogs import DuplicateManagerDialog, MaintenanceDialog, issue_lines
from src.ui.phrase_editor import PhraseEditor
from src.ui.multi_code_editor import MultiCodeEditor
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
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._settings = settings
        self._deploy = deploy
        self._pinyin = pinyin or PinyinService()
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._metadata = metadata_store or MetadataStore(repo.path.parent)
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
        self._deploy_running = False
        self._deploy_pending = False
        self._deploy_results: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        self._deploy_poll = QTimer(self)
        self._deploy_poll.setInterval(100)
        self._deploy_poll.timeout.connect(self._drain_deploy_results)

        self._build_ui()
        self.refresh()
        self._maybe_first_run_auto_group()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._library_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._library_splitter.setChildrenCollapsible(False)
        self._library_splitter.setHandleWidth(5)
        root.addWidget(self._library_splitter)

        # 左侧检索与分组：搜索、排序固定在分组上方，避免压缩主操作栏。
        left_widget = QWidget(self)
        left = QVBoxLayout(left_widget)
        left.setContentsMargins(8, 6, 0, 0)
        left.setSpacing(8)
        left_controls = QWidget(left_widget)
        left_controls.setObjectName("LibrarySideControls")
        left_controls_layout = QVBoxLayout(left_controls)
        left_controls_layout.setContentsMargins(0, 0, 0, 0)
        left_controls_layout.setSpacing(5)
        self._search = SearchBar()
        self._search.setMinimumWidth(0)
        self._search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search.searchChanged.connect(self._on_search)
        self._sort_combo = ClickActivatedComboBox()
        self._sort_combo.addItems(["权重", "拼音", "文本", "加入顺序倒序"])
        self._sort_combo.setCurrentText("加入顺序倒序")
        self._sort_combo.currentTextChanged.connect(self._on_sort_changed)
        left_controls_layout.addWidget(self._search)
        left_controls_layout.addWidget(self._sort_combo)
        left.addWidget(left_controls)
        self._group_panel = GroupPanel(self._groups, count_provider=self._group_counts)
        self._group_panel.groupSelected.connect(self._on_group_selected)
        left.addWidget(self._group_panel, 1)
        left_widget.setMinimumWidth(190)
        left_widget.setMaximumWidth(310)
        self._library_splitter.addWidget(left_widget)

        # 右侧主区
        right_widget = QWidget(self)
        right = QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)

        # 紧凑工具栏：独立容器让其与顶部选项卡清晰分层，不增加垂直高度。
        toolbar_widget = QWidget(self)
        toolbar_widget.setObjectName("PhraseToolbar")
        toolbar = QHBoxLayout(toolbar_widget)
        toolbar.setContentsMargins(8, 5, 8, 5)
        toolbar.setSpacing(6)
        # 批量选择：默认关闭，开启后才显示选择列 + 全选/批量删除
        self._btn_batch_select = QPushButton("批量选择")
        self._btn_batch_select.setObjectName("Primary")
        self._btn_batch_select.clicked.connect(self._on_toggle_batch_select)
        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.clicked.connect(self._on_select_all)
        self._btn_select_all.setVisible(False)
        self._btn_batch_del = QPushButton("批量删除")
        self._btn_batch_del.setObjectName("Danger")
        self._btn_batch_del.clicked.connect(self._on_batch_delete)
        self._btn_batch_del.setVisible(False)
        self._btn_duplicates = QPushButton("显示重码字符/文本")
        self._btn_duplicates.setToolTip("扫描整个词库的同文本多编码和同编码多文本项目")
        self._btn_duplicates.clicked.connect(self._open_duplicates)
        self._filter_combo = ClickActivatedComboBox()
        self._filter_combo.addItems(["全部", "未分组", "英文", "低权重"])
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        self._btn_history = QPushButton("修改历史")
        self._btn_history.clicked.connect(self._open_history)
        self._btn_autogroup = QPushButton("自动分组")
        self._btn_autogroup.clicked.connect(self._on_auto_group)
        self._btn_add = QPushButton("+ 新增")
        self._btn_add.setObjectName("Primary")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_undo = QPushButton("撤销")
        self._btn_undo.clicked.connect(self._on_undo)
        self._btn_save = QPushButton("保存")
        self._btn_save.setObjectName("Primary")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_undo.setEnabled(False)

        toolbar.addWidget(self._btn_duplicates)
        toolbar.addWidget(self._filter_combo)
        toolbar.addWidget(self._btn_history)
        toolbar.addStretch(1)
        toolbar.addWidget(self._btn_batch_select)
        toolbar.addWidget(self._btn_select_all)
        self._btn_batch_group = QPushButton("调整分组")
        self._btn_batch_group.clicked.connect(self._on_batch_group)
        self._btn_batch_group.setVisible(False)
        toolbar.addWidget(self._btn_batch_group)
        toolbar.addWidget(self._btn_batch_del)
        toolbar.addWidget(self._btn_autogroup)
        toolbar.addWidget(self._btn_add)
        toolbar.addWidget(self._btn_undo)
        toolbar.addWidget(self._btn_save)
        right.addWidget(toolbar_widget)

        # 表格
        self._table = PhraseTableView(group_service=self._groups)
        self._table.deleteRequested.connect(self._on_delete)
        self._table.cellEdited.connect(self._on_cell_edited)
        self._table.entryEditRequested.connect(self._on_multi_code_edit)
        self._table.groupEdited.connect(self._on_group_edited)
        right.addWidget(self._table, 1)

        self._status = QLabel("")
        right.addWidget(self._status)

        self._library_splitter.addWidget(right_widget)
        self._library_splitter.setStretchFactor(0, 0)
        self._library_splitter.setStretchFactor(1, 1)
        self._library_splitter.setSizes([190, 890])

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
            self._groups.remap_entry_key(old, p.code, p.text, p.code)
            self._display_store.set(p.text, p.code, display)
        elif col == COL_CODE:
            old = p.code
            display = normalize_display_code(value)
            p.code = raw_code(display)
            self._groups.remap_entry_key(p.text, old, p.text, p.code)
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
        self._status.setText("● 有未保存的修改（点『保存』写盘并按设置部署）")

    def _on_save(self) -> None:
        if not self._dirty:
            return
        self._persist_backup()
        self._repo.save()
        self._groups.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
        self._dirty = False
        self._btn_save.setEnabled(False)
        self._btn_undo.setEnabled(False)
        self.refresh(force=True)
        self._schedule_deploy()
        self.editCompleted.emit(True, self._status.text())

    def _on_undo(self) -> None:
        if not self._dirty:
            return
        # 放弃内存改动：强制从磁盘与 sidecar 重载
        self._repo.load(force=True)
        self._groups.load()
        self._dirty = False
        self._btn_save.setEnabled(False)
        self._btn_undo.setEnabled(False)
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
            self._groups.save()
        return changed

    def _on_auto_group(self) -> None:
        """按钮：仅对未分组条目分组。"""
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
        self._groups.set_entry_group(p.text, p.code, group)  # sidecar 即时保存
        self._table.update_group_cell(row, group)
        self._group_panel.refresh()
        self._status.setText("分组已更新（点『保存』可同步写盘）。")

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
        self._groups.save()
        self._metadata.record("批量调整分组", f"调整为「{group}」", keys)
        self._metadata.save()
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
            self._groups.set_entry_group(text, code, "")
        self._repo.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
        self._metadata.prune(self._repo.all())
        self._metadata.record("批量删除", f"删除 {len(keys)} 条", keys)
        self._metadata.save()
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
            self._groups.set_entry_group(item.text, item.code, "")
        self._repo.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
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
            self._btn_batch_select.setText("批量选择")
        self._table.set_all_checked(False)
        self._all_selected = False
        self._btn_select_all.setText("全选")

    def _open_duplicates(self) -> None:
        DuplicateManagerDialog(self._repo.all(), self._apply_duplicate_group, self, self._display_store.editor_display_for).exec()

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
        self._repo.save()
        self._groups.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
        self._metadata.prune(self._repo.all())
        before = "；".join(f"{item.text}/{self._display_store.display_for(item)}@{item.weight}" for item in original)
        after = "；".join(f"{item.text}/{item.code}@{item.weight}" for item in entries)
        final_keys = {(item.text, raw_code(item.code), item.weight) for item in entries}
        removed = [item for item in original if (item.text, raw_code(item.code), item.weight) not in final_keys]
        removed_text = "；".join(f"{item.text}/{self._display_store.display_for(item)}" for item in removed)
        detail = f"修改前：{before or '无'}；修改后：{after or '无'}"
        if removed_text:
            detail += f"；删除：{removed_text}"
        self._metadata.record("重码集中编辑", detail, [item.key for item in original])
        self._metadata.save()
        self.refresh(force=True)
        self._group_panel.refresh()
        self._schedule_deploy()
        message = f"已集中保存 {len(entries)} 条重码项目。"
        self._status.setProperty("role", "success")
        self._status.setText(message)
        self.editCompleted.emit(True, message)

    def open_health_check(self) -> None:
        MaintenanceDialog("词库检查", issue_lines(health_check(self._repo.all(), self._groups)), self).exec()

    def _open_health(self) -> None:
        self.open_health_check()

    def _open_history(self) -> None:
        lines = [f"{item['at']}  {item['action']}：{item['detail']}" for item in self._metadata.history()]
        MaintenanceDialog("修改历史", lines, self).exec()

    def _selected_phrase(self) -> Phrase | None:
        index = self._table.currentIndex()
        if index.isValid() and 0 <= index.row() < len(self._displayed):
            return self._displayed[index.row()]
        return None


    def _on_add(self) -> None:
        editor = PhraseEditor(
            phrase=None, groups=self._groups.list_groups(),
            pinyin=self._pinyin, repo=self._repo,
            system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._groups.add_group, parent=self,
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

    def _on_multi_code_edit(self, phrase: Phrase) -> None:
        phrases = [item for item in self._repo.all() if item.text == phrase.text]
        editor = MultiCodeEditor(
            phrase.text, phrases, self._repo, self._pinyin,
            self._display_store.display_for, self,
            groups=self._groups.list_groups(),
            group=self._groups.get_entry_group(phrase.text, phrase.code),
            system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._groups.add_group,
        )
        if editor.exec() != editor.DialogCode.Accepted:
            return
        entries = editor.entries()
        updated_text = editor.text_value()
        try:
            group = editor.group_value()
            self._persist_backup()
            for old in phrases:
                self._repo.delete(old.text, old.code)
                self._groups.set_entry_group(old.text, old.code, "")
            for entry in entries:
                code = str(entry["code"])
                weight = self._clamp_weight(int(entry["weight"]))
                self._repo.upsert(updated_text, code, weight)
                self._groups.set_entry_group(updated_text, code, group)
                self._display_store.set(updated_text, code, str(entry["display_code"]))
            self._repo.save()
            self._groups.save()
            self._display_store.prune(self._repo.all())
            self._display_store.save()
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
        weight = self._clamp_weight(int(values["weight"]))
        displays = [str(values.get("display_code", ""))] + list(values.get("additional_codes", []))
        unique: list[str] = []
        for display in displays:
            display = normalize_display_code(display)
            if raw_code(display) and raw_code(display) not in {raw_code(item) for item in unique}:
                unique.append(display)
        if not unique:
            unique = [""]
        self._persist_backup()
        for key, new_weight in values.get("weight_updates", {}).items():
            old_text, separator, old_code = key.partition("\t")
            if separator:
                self._repo.update_weight(old_text, old_code, self._clamp_weight(new_weight))
        phrase = None
        for display in unique:
            code = raw_code(display)
            phrase, _new, _conflict = self._repo.upsert(text, code, weight)
            self._groups.set_entry_group(text, code, group)
            self._display_store.set(text, code, display)
        self._repo.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
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
        self._groups.set_entry_group(text, code, group)
        self._repo.save()
        self._display_store.set(text, code, display_code)
        self._display_store.prune(self._repo.all())
        self._display_store.save()
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
            self._groups.set_entry_group(text, code, group)
        self._repo.save()
        self._display_store.set(text, code, normalize_display_code(code))
        self._display_store.prune(self._repo.all())
        self._display_store.save()
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
    def _persist_backup(self) -> None:
        try:
            self._backup.backup(self.PHRASE_FILE)
            self._backup.backup(DISPLAY_INI_FILENAME)
        except Exception as exc:
            from src.utils.logger import get_logger

            get_logger(__name__).warning("备份失败：%s", exc)

    def reattach(self, repo: PhraseRepo, groups: GroupService,
                 backup: BackupService,
                 system_dictionary_index: SystemDictionaryIndex | None = None,
                 rime_preview_service=None, metadata_store: MetadataStore | None = None) -> None:
        """Reconnect sidecars and views after Rime directory or sandbox changes."""
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._metadata = metadata_store or MetadataStore(repo.path.parent)
        self._display_store = PinyinDisplayStore(repo.path.parent, self._pinyin)
        self._group_panel._groups = groups
        self._group_panel.select_all(emit=False)
        self._group_panel.refresh()
        self._table._groups = groups
        self._table._model._groups = groups
        self._current_group = GroupService.all_group_label()
        self._keyword = ""
        self._content_sig = None
        self._last_sort_mode = None
        self._visibility_key = None
        self.refresh(force=True)

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
        if self._deploy_running:
            self._deploy_pending = True
            self._status.setText("已保存。部署任务正在进行。")
            return

        self._deploy_running = True
        self._status.setText("已保存。正在后台部署…")

        def run() -> None:
            try:
                result = self._deploy.deploy()
            except Exception as exc:
                result = (False, f"部署失败：{exc}")
            self._deploy_results.put(result)

        self._deploy_poll.start()
        threading.Thread(
            target=run,
            name="rime-config-deploy",
            daemon=True,
        ).start()

    def _drain_deploy_results(self) -> None:
        """Apply deploy outcomes in the GUI thread."""
        try:
            ok, msg = self._deploy_results.get_nowait()
        except queue.Empty:
            return
        self._deploy_poll.stop()
        self._deploy_running = False
        self._status.setText(
            "已保存并完成自动部署。" if ok else f"已保存。部署提示：{msg}"
        )
        self._metadata.record("自动部署", "成功" if ok else msg)
        self._metadata.save()
        if self._rime_preview_service is not None:
            if ok:
                self._rime_preview_service.invalidate_after_deploy()
            else:
                self._rime_preview_service.mark_waiting_for_deploy()
        if self._deploy_pending:
            self._deploy_pending = False
            self._schedule_deploy()

    def _maybe_deploy(self) -> tuple[bool | None, str]:
        # 沙盒模式不触发真实部署
        if self._settings.sandbox_mode:
            return None, "沙盒模式，未触发真实部署。"
        if self._settings.auto_deploy:
            return self._deploy.deploy()
        return None, "未开启自动部署。"

    # 供外部（主窗口热键）触发弹窗式快速收藏
    def open_quick_add(self, prefill_text: str = "", notice: str = "") -> None:
        from src.ui.quick_add_dialog import QuickAddDialog

        dlg = QuickAddDialog(
            prefill_text=prefill_text, groups=self._groups.list_groups(),
            pinyin=self._pinyin, repo=self._repo, system_dictionary_index=self._system_dictionary_index,
            rime_preview_service=self._rime_preview_service,
            create_group=self._groups.add_group, notice=notice, parent=None,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            vals = dlg.get_values()
            try:
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
