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

import threading
from typing import List, Optional, Tuple

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.encoding.code_suggestions import normalize_display_code, raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.backup_service import BackupService
from src.service.classification_service import ClassificationService
from src.service.deploy_service import DeployService
from src.service.group_service import GroupService
from src.service.pinyin_service import PinyinService
from src.service.pinyin_display_store import DISPLAY_INI_FILENAME, PinyinDisplayStore
from src.settings import Settings
from src.ui.delete_confirm_dialog import confirm_delete
from src.ui.group_panel import GroupPanel
from src.ui.phrase_editor import PhraseEditor
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

    _deployFinished = Signal(bool, str)

    PHRASE_FILE = "custom_phrase.txt"
    MIN_WEIGHT = 1
    MAX_WEIGHT = 99

    def __init__(self, repo: PhraseRepo, groups: GroupService,
                 backup: BackupService, settings: Settings,
                 deploy: DeployService,
                 pinyin: PinyinService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._settings = settings
        self._deploy = deploy
        self._pinyin = pinyin or PinyinService()
        self._display_store = PinyinDisplayStore(self._repo.path.parent, self._pinyin)
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
        self._batch_mode = False             # 批量选择模式（默认关闭，隐藏选择列）
        self._deploy_running = False
        self._deploy_pending = False
        self._deployFinished.connect(self._on_background_deploy_finished)

        self._build_ui()
        self.refresh()
        self._maybe_first_run_auto_group()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setSpacing(10)

        # 左侧分组
        self._group_panel = GroupPanel(self._groups)
        self._group_panel.groupSelected.connect(self._on_group_selected)
        root.addWidget(self._group_panel, 1)

        # 右侧主区
        right = QVBoxLayout()
        right.setSpacing(8)

        # 工具栏
        toolbar = QHBoxLayout()
        self._search = SearchBar()
        self._search.setMaximumWidth(480)  # 搜索框宽度（较此前翻倍）
        self._search.searchChanged.connect(self._on_search)
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
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["权重", "拼音", "文本", "加入顺序倒序"])
        self._sort_combo.setCurrentText("加入顺序倒序")
        self._btn_duplicates = QPushButton("仅重码")
        self._btn_duplicates.setCheckable(True)
        self._btn_duplicates.setToolTip("只显示编码相同的并列候选，双击权重可快速调整")
        self._btn_duplicates.toggled.connect(self._on_duplicate_toggled)
        self._sort_combo.currentTextChanged.connect(self._on_sort_changed)
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

        toolbar.addWidget(self._search)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel("排序："))
        toolbar.addWidget(self._sort_combo)
        toolbar.addWidget(self._btn_duplicates)
        toolbar.addStretch(1)
        toolbar.addWidget(self._btn_select_all)
        toolbar.addWidget(self._btn_batch_del)
        toolbar.addWidget(self._btn_autogroup)
        toolbar.addWidget(self._btn_add)
        toolbar.addWidget(self._btn_undo)
        toolbar.addWidget(self._btn_save)
        right.addLayout(toolbar)

        # 表格
        self._table = PhraseTableView(group_service=self._groups)
        self._table.deleteRequested.connect(self._on_delete)
        self._table.cellEdited.connect(self._on_cell_edited)
        self._table.groupEdited.connect(self._on_group_edited)
        right.addWidget(self._table, 1)

        self._status = QLabel("")
        right.addWidget(self._status)

        root.addLayout(right, 4)

    # ------------------------------------------------------------------ #
    # 过滤 + 刷新（显隐模式：全量仅首次/数据变更时重建，切分组/搜索只显隐）
    # ------------------------------------------------------------------ #
    def refresh(self, force: bool = False) -> None:
        content_sig = self._content_signature(self._all_phrases)
        need_rebuild = (
            force
            or not self._all_phrases
            or self._dirty
            or self._content_sig != content_sig
        )
        if need_rebuild:
            # 内容变化：重建 model（beginResetModel，无 QWidget 开销）
            self._all_phrases = self._sort_phrases(self._repo.search(""))
            self._table.set_phrases(
                self._all_phrases,
                group_of=self._group_of,
                selected_of=lambda p: p.key in (self._checked_keys()),
                code_display=self._display_store.display_for,
            )
            self._content_sig = self._content_signature(self._all_phrases)
        else:
            # 仅排序变化：轻量重排（layoutChanged，零控件重建，彻底流畅）
            self._all_phrases = self._sort_phrases(self._repo.search(""))
            self._table.reorder(self._all_phrases)
        # 分组 / 搜索仅显隐，不重建表格（消除切分组卡顿）
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
        if not kw and not self._duplicate_only and grp == GroupService.all_group_label():
            self._table.apply_filter(None)
            self._displayed = list(self._all_phrases)
            return
        wanted = (
            set(self._groups.entries_of(grp))
            if grp != GroupService.all_group_label() else None
        )
        code_counts: dict[str, int] = {}
        if self._duplicate_only:
            for phrase in self._all_phrases:
                code = raw_code(phrase.code)
                code_counts[code] = code_counts.get(code, 0) + 1
        visible = []
        for p in self._all_phrases:
            if kw and kw not in p.text.lower() and kw not in p.code.lower():
                continue
            if self._duplicate_only and code_counts.get(raw_code(p.code), 0) < 2:
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
        k = key_map.get(self._sort_combo.currentText(), "weight")
        rev = (k in ("weight", "order"))
        return self._repo.sort_by(k, reverse=rev)

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
        ok, msg = self._maybe_deploy()
        if ok is True:
            self._status.setText("已保存并自动部署。")
        elif ok is False:
            self._status.setText(f"已保存。部署提示：{msg}")
        else:
            self._status.setText(f"已保存。{msg}")
        self.refresh(force=True)

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
        self._btn_batch_select.setText("退出批量" if self._batch_mode else "批量选择")
        if not self._batch_mode:
            self._table.set_all_checked(False)
            self._all_selected = False
            self._btn_select_all.setText("全选")
            self._status.setText("已退出批量选择。")

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
        self._maybe_deploy()
        self._all_selected = False
        self._btn_select_all.setText("全选")
        self.refresh(force=True)
        self._status.setText(f"已删除 {len(keys)} 条。")

    # ------------------------------------------------------------------ #
    # 单条删除（保留确认）
    # ------------------------------------------------------------------ #
    def _on_delete(self, phrase: Phrase) -> None:
        if not confirm_delete(
            self,
            "删除词条",
            f"确认删除「{phrase.text} / {phrase.code}」？",
        ):
            return
        self._persist_backup()
        self._repo.delete(phrase.text, phrase.code)
        self._groups.set_entry_group(phrase.text, phrase.code, "")
        self._repo.save()
        self._display_store.prune(self._repo.all())
        self._display_store.save()
        self._maybe_deploy()
        self.refresh(force=True)

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


    def _on_duplicate_toggled(self, checked: bool) -> None:
        self._duplicate_only = checked
        self._btn_duplicates.setText("退出重码" if checked else "仅重码")
        self.refresh()
    def _on_add(self) -> None:
        editor = PhraseEditor(
            phrase=None, groups=self._groups.list_groups(),
            pinyin=self._pinyin, repo=self._repo, parent=self,
        )
        if self._current_group != GroupService.all_group_label():
            editor.set_group_hint(self._current_group)
        if editor.exec() == editor.DialogCode.Accepted:
            vals = editor.get_values()
            self._apply_upsert(vals["text"], vals["code"], vals["weight"],
                               vals["group"], is_new=True,
                               display_code=vals.get("display_code", ""),
                               weight_updates=vals.get("weight_updates"))

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
            # Exact duplicates retain their current weight unless the user
            # explicitly changed that entry in the inline conflict editor.
            weight = existing.weight
        self._persist_backup()
        phrase, _new, conflict = self._repo.upsert(text, code, weight)
        # 分组归属
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
        else:
            self._maybe_deploy()

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
                 backup: BackupService) -> None:
        """Reconnect sidecars and views after Rime directory or sandbox changes."""
        self._repo = repo
        self._groups = groups
        self._backup = backup
        self._display_store = PinyinDisplayStore(repo.path.parent, self._pinyin)
        self._group_panel._groups = groups
        self._group_panel.select_all(emit=False)
        self._group_panel.refresh()
        self._table._groups = groups
        self._table._model._groups = groups
        self._current_group = GroupService.all_group_label()
        self._keyword = ""
        self._content_sig = None
        self.refresh(force=True)

    def _schedule_deploy(self) -> None:
        """将自动部署移出保存和刷新主路径，避免阻塞界面。"""
        if self._settings.sandbox_mode:
            self._status.setText("已保存到沙盒副本。")
            return
        if not self._settings.auto_deploy:
            self._status.setText("已保存。未开启自动部署。")
            return
        if self._deploy_running:
            self._deploy_pending = True
            self._status.setText("已保存。部署任务正在进行。")
            return

        self._deploy_running = True
        self._status.setText("已保存。正在后台部署…")

        def run() -> None:
            ok, msg = self._deploy.deploy()
            self._deployFinished.emit(ok, msg)

        threading.Thread(
            target=run,
            name="rime-config-deploy",
            daemon=True,
        ).start()

    def _on_background_deploy_finished(self, ok: bool, msg: str) -> None:
        self._deploy_running = False
        self._status.setText(
            "已保存并完成自动部署。" if ok else f"已保存。部署提示：{msg}"
        )
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
            pinyin=self._pinyin, repo=self._repo, notice=notice, parent=self,
        )
        if dlg.exec() == dlg.DialogCode.Accepted:
            vals = dlg.get_values()
            self._apply_upsert(vals["text"], vals["code"], vals["weight"],
                               vals["group"], is_new=True, reset_view=True,
                               display_code=vals.get("display_code", ""),
                               weight_updates=vals.get("weight_updates"))
