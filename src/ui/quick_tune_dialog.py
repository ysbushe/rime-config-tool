"""Bidirectional workbench for finding and ranking managed phrases."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.encoding.code_suggestions import raw_code
from src.repo.phrase_repo import Phrase, PhraseRepo
from src.service.pinyin_service import PinyinService
from src.service.quick_tune_session import QuickTuneSession, TuneCandidate
from src.service.system_dictionary_index import SystemDictionaryIndex
from src.ui.dialog_workbench import dialog_section
from src.ui.index_progress import present_index_progress


SaveCallback = Callable[[list[Phrase], tuple[int, int] | None], tuple[bool, str]]


class ExactSearchLineEdit(QLineEdit):
    """Notify the dialog as soon as an IME preedit starts."""

    preeditStarted = Signal()

    def inputMethodEvent(self, event) -> None:
        if event.preeditString():
            self.preeditStarted.emit()
        super().inputMethodEvent(event)


class QuickTuneDialog(QDialog):
    """Select or query a phrase, preview both directions, then adjust its rank."""

    def __init__(
            self, repo: PhraseRepo, pinyin: PinyinService,
            preview_service=None, save_changes: SaveCallback | None = None,
            initial_phrase: Phrase | None = None, parent=None,
            dictionary_index: SystemDictionaryIndex | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("字词快速调节")
        self.resize(860, 620)
        self._source_repo = repo
        self._pinyin = pinyin
        self._preview_service = preview_service
        self._dictionary_index = dictionary_index
        self._session = QuickTuneSession(repo, pinyin, dictionary_index)
        self._repo = self._session.repo
        if self._dictionary_index is not None and hasattr(
                self._dictionary_index, "ensure_ready_async"):
            self._dictionary_index.ensure_ready_async()
        self._save_changes = save_changes
        self._source_signature = self._file_signature()
        self._selected_key = ""
        self._selected_candidate: TuneCandidate | None = None
        self._mode = "code"
        self._active_code = ""
        self._preview_buttons: list[QPushButton] = []
        self._preview_items: list[TuneCandidate] = []
        self._candidate_limit = max(
            1, min(10, int(getattr(preview_service, "page_size", 5)))
        )
        self._rebuilding_table = False
        self._disposed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        chooser, chooser_layout = dialog_section(self, "精确查找字词或拼音编码")
        self._search = ExactSearchLineEdit(chooser)
        self._search.setPlaceholderText("精确输入完整字词或完整拼音编码")
        self._search.textChanged.connect(self._on_search_changed)
        self._search.preeditStarted.connect(self._on_search_preedit)
        chooser_layout.addWidget(self._search)
        self._table = QTableWidget(0, 4, chooser)
        self._table.setHorizontalHeaderLabels(["字词", "拼音编码", "来源", "权重"])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setMinimumHeight(205)
        self._table.itemSelectionChanged.connect(self._use_table_selection)
        chooser_layout.addWidget(self._table)
        root.addWidget(chooser, 1)

        query, query_layout = dialog_section(self, "当前调节项")
        query_row = QHBoxLayout()
        code_column = QVBoxLayout()
        code_column.addWidget(QLabel("已选拼音编码"))
        self._code_input = QLineEdit(query)
        self._code_input.setPlaceholderText("请先从上方精确查找结果中选择")
        self._code_input.setReadOnly(True)
        code_column.addWidget(self._code_input)
        text_column = QVBoxLayout()
        text_column.addWidget(QLabel("已选字词"))
        self._text_input = QLineEdit(query)
        self._text_input.setPlaceholderText("请先从上方精确查找结果中选择")
        self._text_input.setReadOnly(True)
        text_column.addWidget(self._text_input)
        query_row.addLayout(code_column, 1)
        query_row.addLayout(text_column, 1)
        query_layout.addLayout(query_row)
        query.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        query.setFixedHeight(query.sizeHint().height())
        self._query_section = query
        root.addWidget(query, 0)

        preview, preview_layout = dialog_section(self, "实际输入法候选预览")
        self._preedit = QLabel("选择词条后读取实际输入法候选。", preview)
        self._preedit.setObjectName("RimePreviewStatus")
        self._preedit.setWordWrap(False)
        self._preedit.setFixedHeight(self._preedit.fontMetrics().lineSpacing() + 4)
        preview_layout.addWidget(self._preedit)
        self._candidate_box = QWidget(preview)
        self._candidate_layout = QHBoxLayout(self._candidate_box)
        self._candidate_layout.setContentsMargins(0, 4, 0, 4)
        self._candidate_layout.setSpacing(6)
        self._candidate_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for index in range(self._candidate_limit):
            button = QPushButton("", self._candidate_box)
            button.setMinimumWidth(100)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, item_index=index:
                self._choose_preview_item(item_index)
            )
            button.hide()
            self._candidate_layout.addWidget(button, 1)
            self._preview_buttons.append(button)
        self._candidate_box.setFixedHeight(max(40, button.sizeHint().height() + 8))
        preview_layout.addWidget(self._candidate_box)
        self._preview_note = QLabel(
            "静态词典只用于精确查找；这里不会用静态排序替代实际输入法候选。",
            preview,
        )
        self._preview_note.setWordWrap(True)
        self._preview_note.setFixedHeight(
            self._preview_note.fontMetrics().lineSpacing() * 2 + 6
        )
        self._preview_note.setProperty("role", "info")
        preview_layout.addWidget(self._preview_note)
        preview.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        preview.setFixedHeight(preview.sizeHint().height())
        self._preview_section = preview
        root.addWidget(preview, 0)

        actions = QHBoxLayout()
        self._top_button = QPushButton("置顶", self)
        self._up_button = QPushButton("上移", self)
        self._down_button = QPushButton("下移", self)
        self._top_button.clicked.connect(lambda: self._run_rank("top"))
        self._up_button.clicked.connect(lambda: self._run_rank("up"))
        self._down_button.clicked.connect(lambda: self._run_rank("down"))
        self._save_button = QPushButton("保存", self)
        self._save_button.setObjectName("Primary")
        self._save_button.clicked.connect(self._save)
        self._save_button.setEnabled(False)
        self._close_button = QPushButton("关闭", self)
        self._close_button.clicked.connect(self.reject)
        actions.addWidget(self._top_button)
        actions.addWidget(self._up_button)
        actions.addWidget(self._down_button)
        actions.addStretch(1)
        actions.addWidget(self._save_button)
        actions.addWidget(self._close_button)
        root.addLayout(actions)

        self._index_progress = QProgressBar(self)
        self._index_progress.setRange(0, 100)
        self._index_progress.setTextVisible(True)
        self._index_progress.hide()
        root.addWidget(self._index_progress)

        self._status = QLabel("请在上方精确查找并选择需要调节的词条。", self)
        self._status.setWordWrap(True)
        self._status.setFixedHeight(self._status.fontMetrics().lineSpacing() * 2 + 4)
        root.addWidget(self._status)

        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(120)
        self._request_timer.timeout.connect(self._request_engine_preview)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self._refresh_table)
        self._index_state = str(getattr(self._dictionary_index, "state", ""))
        self._index_poll = QTimer(self)
        self._index_poll.setInterval(250)
        self._index_poll.timeout.connect(self._poll_dictionary_index)
        if self._dictionary_index is not None and self._index_state in {
                "building", "cancelling", "unavailable"}:
            self._index_poll.start()
        if self._preview_service is not None and hasattr(
                self._preview_service, "events"):
            self._preview_service.events.changed.connect(self._refresh_engine_preview)

        self._refresh_table()
        self._update_action_state()
        self._poll_dictionary_index()
        if initial_phrase is not None:
            self._select_phrase(initial_phrase)

    def select_candidate(self, text: str, code: str) -> bool:
        """Open an exact dictionary result in this workbench."""
        self._search.setText(text)
        self._search_timer.stop()
        self._refresh_table()
        expected_code = raw_code(code).casefold()
        fallback_row = -1
        for row in range(self._table.rowCount()):
            text_item = self._table.item(row, 0)
            code_item = self._table.item(row, 1)
            if text_item is None or code_item is None or text_item.text() != text:
                continue
            if fallback_row < 0:
                fallback_row = row
            if raw_code(code_item.text()).casefold() == expected_code:
                self._table.selectRow(row)
                return True
        if fallback_row >= 0:
            self._table.selectRow(fallback_row)
            return True
        return False

    @property
    def _dirty(self) -> bool:
        return self._session.dirty

    @_dirty.setter
    def _dirty(self, value: bool) -> None:
        self._session.dirty = bool(value)

    def _refresh_table(self, _text: str = "", select_key: str = "") -> None:
        keyword = self._search.text().strip().lower()
        rows = self._search_rows(keyword)
        self._rebuilding_table = True
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._table.setRowCount(len(rows))
        selected_row = -1
        for row, (candidate, weight) in enumerate(rows):
            text_item = QTableWidgetItem(candidate.text)
            text_item.setData(Qt.ItemDataRole.UserRole, candidate.key)
            text_item.setData(Qt.ItemDataRole.UserRole + 1, candidate.text)
            text_item.setData(Qt.ItemDataRole.UserRole + 2, candidate.code)
            text_item.setData(Qt.ItemDataRole.UserRole + 3, candidate.source)
            self._table.setItem(row, 0, text_item)
            self._table.setItem(row, 1, QTableWidgetItem(candidate.code))
            self._table.setItem(row, 2, QTableWidgetItem(candidate.source))
            weight_item = QTableWidgetItem("—" if weight is None else str(weight))
            weight_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, weight_item)
            if candidate.key == select_key:
                selected_row = row
        self._rebuilding_table = False
        if selected_row >= 0:
            self._table.selectRow(selected_row)
            self._table.scrollToItem(self._table.item(selected_row, 0))

    def _on_search_changed(self, _value: str) -> None:
        """A new exact query starts a fresh selection and preview context."""
        self._clear_search_context()
        self._search_timer.start()

    def _on_search_preedit(self) -> None:
        """Do not leave stale rows visible under the native IME candidate window."""
        self._search_timer.stop()
        self._clear_search_context()

    def _clear_search_context(self) -> None:
        self._request_timer.stop()
        self._selected_key = ""
        self._selected_candidate = None
        self._mode = "code"
        self._active_code = ""
        self._code_input.clear()
        self._text_input.clear()
        self._show_preview_items(
            "选择词条后读取实际输入法候选。",
            [],
            "请从上方精确搜索结果中选择词条。",
        )
        self._set_status("搜索条件已更新，请选择精确匹配的词条。", "info")
        self._update_action_state()
        self._rebuilding_table = True
        self._table.clearSelection()
        self._table.setCurrentCell(-1, -1)
        self._table.setRowCount(0)
        self._rebuilding_table = False

    def _search_rows(
            self, keyword: str) -> list[tuple[TuneCandidate, int | None]]:
        return self._session.search(keyword)

    def _poll_dictionary_index(self) -> None:
        state = str(getattr(self._dictionary_index, "state", ""))
        if state in {"building", "cancelling"}:
            progress = dict(getattr(self._dictionary_index, "progress", {}) or {})
            view = present_index_progress(progress)
            self._index_progress.setValue(view.percent)
            self._index_progress.show()
            verb = "正在取消索引构建" if state == "cancelling" else "正在后台建立当前方案索引"
            self._set_status(
                f"{verb}（{view.percent}%，{view.files_completed}/"
                f"{view.files_total} 个词典文件）；"
                "完成前仅显示个人词库结果。",
                "info",
            )
            return
        if state == self._index_state:
            return
        self._index_state = state
        if state == "ready":
            self._index_poll.stop()
            self._index_progress.hide()
            if self._search.text().strip():
                self._refresh_table()
                self._set_status("当前方案索引已就绪，请选择精确匹配的词条。", "success")
        elif state in {"failed", "cancelled"}:
            self._index_poll.stop()
            self._index_progress.hide()
            self._set_status("当前方案索引未能建立；暂时只显示个人词库结果。", "warning")

    def _use_table_selection(self) -> None:
        if self._rebuilding_table:
            return
        row = self._table.currentRow()
        if row < 0 or self._table.item(row, 0) is None:
            return
        key = str(self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) or "")
        phrase = self._phrase_for_key(key)
        if phrase is not None:
            self._select_phrase(phrase, select_table=False)
            return
        item = self._table.item(row, 0)
        candidate = TuneCandidate(
            str(item.data(Qt.ItemDataRole.UserRole + 1) or ""),
            str(item.data(Qt.ItemDataRole.UserRole + 1) or ""),
            str(item.data(Qt.ItemDataRole.UserRole + 2) or ""),
            source=str(item.data(Qt.ItemDataRole.UserRole + 3) or "系统词库"),
        )
        self._code_input.setText(candidate.code)
        self._text_input.setText(candidate.text)
        self._preview_from_code(candidate.code, preferred_text=candidate.text)
        candidate = next(
            (
                preview_item for preview_item in self._preview_items
                if preview_item.text == candidate.text
                and preview_item.code == candidate.code
            ),
            candidate,
        )
        self._selected_candidate = candidate
        self._selected_key = ""
        self._set_status(
            f"已选择{candidate.source}词条；首次调节会加入个人词库，不修改原词典。",
            "info",
        )
        self._update_action_state()

    def _select_phrase(self, phrase: Phrase, *, select_table: bool = True) -> None:
        self._selected_key = phrase.key
        self._selected_candidate = TuneCandidate(
            phrase.text, phrase.text, phrase.code, phrase.key
        )
        self._code_input.setText(phrase.code)
        self._text_input.setText(phrase.text)
        if select_table:
            self._select_table_key(phrase.key)
        self._preview_from_code(phrase.code, preferred_text=phrase.text)
        self._update_action_state()

    def _select_table_key(self, key: str) -> None:
        """Select an existing row without rebuilding the table or preview area."""
        self._rebuilding_table = True
        try:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                    self._table.selectRow(row)
                    self._table.scrollToItem(item)
                    return
        finally:
            self._rebuilding_table = False

    def _preview_from_code(
            self, value: str, *, preferred_text: str = "") -> None:
        self._mode = "code"
        self._active_code = raw_code(value)
        preferred_key = self._key_for(preferred_text, self._active_code)
        self._selected_key = preferred_key
        previous = self._selected_candidate
        selected = None
        if preferred_key:
            phrase = self._phrase_for_key(preferred_key)
            if phrase is not None:
                selected = TuneCandidate(
                    phrase.text, phrase.text, phrase.code, phrase.key
                )
        elif (
            previous is not None
            and previous.text == preferred_text
            and previous.code == self._active_code
        ):
            selected = previous
        if self._dirty:
            note = "修改已暂存；保存并完成部署后才能读取更新后的实际候选。"
        elif self._preview_service is None:
            note = "未配置实际输入法候选预览服务；静态词典结果不会在此处冒充实际候选。"
        elif getattr(self._preview_service, "schema_id", None) is None:
            note = getattr(
                self._preview_service,
                "availability_message",
                "当前方案无法安全读取实际候选。",
            )
            note += " 静态词典结果不会在此处替代显示。"
        else:
            note = "正在读取独立 Rime 会话的实际候选……"
        self._show_preview_items(
            f"输入：{value or '—'}", [], note,
            preferred_key=preferred_key,
        )
        self._selected_candidate = selected
        self._update_action_state()
        self._request_timer.stop()
        if (
            self._active_code
            and self._preview_service is not None
            and not self._dirty
            and getattr(self._preview_service, "schema_id", None) is not None
        ):
            self._request_timer.start()

    def _show_preview_items(
            self, preedit: str, items: list[TuneCandidate], note: str,
            *, preferred_key: str = "") -> None:
        self._preview_items = items[:self._candidate_limit]
        self._preedit.setText(preedit)
        self._preview_note.setText(note)
        for index, button in enumerate(self._preview_buttons):
            if index >= len(self._preview_items):
                button.hide()
                button.setChecked(False)
                continue
            candidate = self._preview_items[index]
            button.setText(f"{index + 1}. {candidate.label}")
            button.setToolTip(f"{candidate.label} · {candidate.source}")
            button.setChecked(bool(candidate.key and candidate.key == preferred_key))
            button.show()

    def _choose_preview_item(self, index: int) -> None:
        if not 0 <= index < len(self._preview_items):
            return
        candidate = self._preview_items[index]
        self._text_input.setText(candidate.text)
        phrase = self._phrase_for_key(candidate.key)
        self._selected_candidate = candidate
        self._selected_key = phrase.key if phrase is not None else ""
        for item_index, button in enumerate(self._preview_buttons):
            button.setChecked(
                item_index < len(self._preview_items)
                and self._preview_items[item_index].text == candidate.text
                and self._preview_items[item_index].code == candidate.code
            )
        if phrase is None:
            self._set_status(
                f"已选择{candidate.source}词条；首次调节会加入个人词库，不修改原词典。",
                "info",
            )
            self._update_action_state()
            return
        self._code_input.setText(phrase.code)
        self._text_input.setText(phrase.text)
        self._select_table_key(phrase.key)
        self._set_status("已选择个人词库词条，可置顶、上移或下移。", "success")
        self._update_action_state()

    def _request_engine_preview(self) -> None:
        if self._preview_service is not None and self._active_code:
            self._preview_service.request(self._active_code)

    def _refresh_engine_preview(self) -> None:
        if (
            self._disposed
            or self._dirty
            or self._mode != "code"
            or self._preview_service is None
        ):
            return
        snapshot = self._preview_service.snapshot
        if snapshot.code and snapshot.code != self._active_code:
            return
        if snapshot.state in {"loading", "querying", "stale"}:
            self._preview_note.setText("正在读取实际 Rime 候选……")
            return
        if snapshot.state == "unavailable":
            self._show_preview_items(
                f"输入：{self._code_input.text() or '—'}",
                [],
                f"实际 Rime 预览暂不可用：{snapshot.message} 静态词典结果不会替代显示。",
                preferred_key=self._selected_key,
            )
            return
        if snapshot.state == "waiting_deploy":
            self._preview_note.setText(
                "排序已保存；重新部署后，实际 Rime 候选会按最新配置刷新。"
            )
            return
        if snapshot.state != "ready":
            return
        indexed = {
            item.text: item for item in self._code_candidates(self._active_code)
        }
        items: list[TuneCandidate] = []
        for candidate in snapshot.candidates:
            phrase = self._repo.find(candidate.text, self._active_code)
            known = indexed.get(candidate.text)
            items.append(TuneCandidate(
                candidate.text, candidate.text, self._active_code,
                phrase.key if phrase is not None else "",
                "个人词库" if phrase is not None
                else (known.source if known is not None else "Rime 候选"),
            ))
        self._show_preview_items(
            f"实际输入：{self._code_input.text() or '—'}",
            items,
            "当前显示独立测试环境生成的候选；任一词条均可调节，"
            "系统项会保存为个人覆盖项。"
            + (f" {snapshot.message}" if snapshot.message else ""),
            preferred_key=self._selected_key,
        )

    def _run_rank(self, action: str) -> None:
        candidate = self._selected_candidate
        if candidate is None:
            return
        changed, message = self._stage_rank(
            candidate.text, candidate.code, action
        )
        self._set_status(message, "success" if changed else "info")
        phrase = self._repo.find(candidate.text, candidate.code)
        if phrase is None:
            self._selected_key = ""
            self._selected_candidate = None
            self._refresh_table()
            self._update_action_state()
            return
        self._refresh_table(select_key=phrase.key)
        self._preview_from_code(phrase.code, preferred_text=phrase.text)
        self._selected_key = phrase.key
        self._selected_candidate = TuneCandidate(
            phrase.text, phrase.text, phrase.code, phrase.key
        )
        self._update_action_state()

    def _stage_rank(
            self, text: str, code: str, action: str) -> tuple[bool, str]:
        changed, message = self._session.stage_rank(text, code, action)
        if changed:
            self._save_button.setEnabled(self._save_changes is not None)
        return changed, message

    def _save(self) -> None:
        if not self._dirty or self._save_changes is None:
            return
        try:
            changed, message = self._save_changes(
                self._repo.all(), self._source_signature
            )
        except Exception as exc:
            self._set_status(f"保存失败，已保留当前暂存调整：{exc}", "error")
            self._save_button.setEnabled(True)
            return
        self._set_status(message, "success" if changed else "warning")
        if not changed:
            return
        self._dirty = False
        self._save_button.setEnabled(False)
        self._source_signature = self._file_signature()
        self._set_status(message, "success")

    def _set_status(self, text: str, role: str) -> None:
        self._status.setText(text)
        self._status.setProperty("role", role)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _update_action_state(self) -> None:
        enabled = bool(self._selected_candidate)
        for button in (self._top_button, self._up_button, self._down_button):
            button.setEnabled(enabled)

    def _file_signature(self) -> tuple[int, int] | None:
        try:
            stat = self._source_repo.path.stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def _code_candidates(self, code: str) -> list[TuneCandidate]:
        return self._session.code_candidates(code, self._candidate_limit)

    def _phrase_for_key(self, key: str) -> Phrase | None:
        text, separator, code = key.partition("\t")
        return self._repo.find(text, code) if separator else None

    def _key_for(self, text: str, code: str) -> str:
        phrase = self._repo.find(text, code) if text and code else None
        return phrase.key if phrase is not None else ""

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._search_timer.stop()
        self._request_timer.stop()
        self._index_poll.stop()
        if self._preview_service is not None and hasattr(
                self._preview_service, "events"):
            try:
                self._preview_service.events.changed.disconnect(
                    self._refresh_engine_preview
                )
            except (RuntimeError, TypeError):
                pass

    def reject(self) -> None:
        if not self._confirm_discard():
            return
        self.dispose()
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.dispose()
        super().closeEvent(event)

    def _confirm_discard(self) -> bool:
        if not self._dirty or not self.isVisible():
            return True
        answer = QMessageBox.question(
            self,
            "放弃未保存修改？",
            "当前排序调整尚未保存，关闭窗口将放弃这些修改。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes
