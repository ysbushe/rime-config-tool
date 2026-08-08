"""Scheme-scoped browser and safe editor for installed Rime dictionaries."""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.service.dictionary_catalog import (
    DictionaryCatalogItem,
    DictionaryCatalogService,
    DictionaryEntry,
    input_code,
)
from src.service.rime_profiles import scheme_title
from src.service.data_engine_service import DataEngineClient
from src.service.backup_service import BackupService
from src.service.dictionary_edit_session import DictionaryEditSession
from src.service.file_transaction import TransactionalFileWriter


TuneCallback = Callable[[str, str], None]
SavedCallback = Callable[[str], None]


class DictionaryCenterDialog(QDialog):
    """Browse dictionaries for one active schema; edits are delegated as overrides."""

    def __init__(
            self, rime_dir, tune_entry: TuneCallback | None = None,
            scope: str = "all", parent=None,
            data_engine_client: DataEngineClient | None = None,
            transaction_writer: TransactionalFileWriter | None = None,
            backup_service: BackupService | None = None,
            saved_callback: SavedCallback | None = None,
            embedded: bool = False) -> None:
        super().__init__(parent)
        self._embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setObjectName("AppDialog")
        self.setWindowTitle("当前方案词典中心")
        self.resize(1000, 700)
        self._service = DictionaryCatalogService(rime_dir)
        self._rime_dir = str(self._service.root)
        self._data_engine_client = data_engine_client
        self._engine_channel = f"dictionary-center-{id(self)}"
        self._tune_entry = tune_entry
        self._transaction_writer = transaction_writer
        self._backup_service = backup_service
        self._saved_callback = saved_callback
        self._scope = scope
        self._catalog: list[DictionaryCatalogItem] = []
        self._selected_item: DictionaryCatalogItem | None = None
        self._request_id = 0
        self._next_cursor: dict | None = None
        self._loading_append = False
        self._results: queue.SimpleQueue = queue.SimpleQueue()
        self._edit_session: DictionaryEditSession | None = None
        self._rebuilding_entries = False
        self._catalog_row = -1

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        page_header = QWidget(self)
        page_header.setObjectName("PageHeader")
        page_header_layout = QVBoxLayout(page_header)
        page_header_layout.setContentsMargins(0, 0, 0, 0)
        page_header_layout.setSpacing(3)
        page_title = QLabel("词典中心", page_header)
        page_title.setObjectName("PageTitle")
        page_subtitle = QLabel(
            "按当前输入方案浏览系统词典、扩展词库和用户词典",
            page_header,
        )
        page_subtitle.setObjectName("PageSubtitle")
        page_header_layout.addWidget(page_title)
        page_header_layout.addWidget(page_subtitle)
        root.addWidget(page_header)

        scheme_row = QHBoxLayout()
        scheme_row.addWidget(QLabel("当前方案：", self))
        self._scheme = QComboBox(self)
        for schema_id in self._service.available_schemes():
            self._scheme.addItem(scheme_title(schema_id), schema_id)
        preferred = self._service.preferred_scheme()
        for index in range(self._scheme.count()):
            if self._scheme.itemData(index) == preferred:
                self._scheme.setCurrentIndex(index)
                break
        self._scheme_index = self._scheme.currentIndex()
        self._scheme.currentIndexChanged.connect(self._on_scheme_changed)
        scheme_row.addWidget(self._scheme)
        self._scheme_note = QLabel(self)
        self._scheme_note.setProperty("role", "info")
        scheme_row.addWidget(self._scheme_note, 1)
        root.addLayout(scheme_row)

        self._catalog_table = QTableWidget(0, 4, self)
        self._catalog_table.setHorizontalHeaderLabels(
            ["词典", "原文件名", "用途", "类型"]
        )
        self._configure_table(self._catalog_table)
        self._catalog_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._catalog_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._catalog_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._catalog_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._catalog_table.setMinimumHeight(220)
        self._catalog_table.itemSelectionChanged.connect(
            self._on_catalog_selection
        )
        root.addWidget(self._catalog_table, 1)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("词条筛选：", self))
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("输入完整字词或完整拼音编码（精确匹配）")
        self._search.textChanged.connect(self._schedule_entries)
        search_row.addWidget(self._search, 1)
        self._load_more = QPushButton("加载更多", self)
        self._load_more.setEnabled(False)
        self._load_more.clicked.connect(lambda: self._load_entries(append=True))
        search_row.addWidget(self._load_more)
        root.addLayout(search_row)

        self._entry_table = QTableWidget(0, 4, self)
        self._entry_table.setHorizontalHeaderLabels(
            ["字词", "拼音编码", "权重", "来源原文件"]
        )
        self._configure_table(self._entry_table)
        for column in (0, 1, 3):
            self._entry_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.Stretch
            )
        self._entry_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._entry_table.itemDoubleClicked.connect(
            lambda _item: self._tune_selected()
        )
        self._entry_table.itemChanged.connect(self._on_editable_item_changed)
        root.addWidget(self._entry_table, 2)

        actions = QHBoxLayout()
        self._status = QLabel(self)
        self._status.setWordWrap(True)
        self._status.setProperty("role", "info")
        actions.addWidget(self._status, 1)
        self._tune_button = QPushButton("调节所选词条", self)
        self._tune_button.setObjectName("Primary")
        self._tune_button.clicked.connect(self._tune_selected)
        self._tune_button.setEnabled(False)
        self._add_button = QPushButton("新增词条", self)
        self._add_button.clicked.connect(self._add_editable_entry)
        self._delete_button = QPushButton("删除所选", self)
        self._delete_button.setObjectName("Danger")
        self._delete_button.clicked.connect(self._delete_editable_entry)
        self._undo_button = QPushButton("撤销", self)
        self._undo_button.clicked.connect(self._undo_editable_changes)
        self._save_button = QPushButton("保存", self)
        self._save_button.setObjectName("Primary")
        self._save_button.clicked.connect(self._save_editable_changes)
        self._close_button = QPushButton("关闭", self)
        self._close_button.clicked.connect(self.accept)
        self._close_button.setVisible(not embedded)
        for button in (
            self._add_button, self._delete_button,
            self._undo_button, self._save_button,
        ):
            button.hide()
        actions.addWidget(self._tune_button)
        actions.addWidget(self._add_button)
        actions.addWidget(self._delete_button)
        actions.addWidget(self._undo_button)
        actions.addWidget(self._save_button)
        actions.addWidget(self._close_button)
        root.addLayout(actions)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._load_entries)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_results)
        self._poll_timer.start()
        self._entry_table.itemSelectionChanged.connect(self._update_tune_state)
        self._refresh_catalog()

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    @property
    def _editable_dirty(self) -> bool:
        """Compatibility view retained for interaction tests and dirty guards."""
        return bool(self._edit_session and self._edit_session.dirty)

    def _refresh_catalog(self) -> None:
        schema_id = str(self._scheme.currentData() or "")
        items = self._service.catalog(schema_id)
        if self._scope == "extensions":
            items = [item for item in items if item.kind != "系统词典入口"]
        self._catalog = items
        self._catalog_row = -1
        self._selected_item = None
        self._leave_editable_mode()
        self._next_cursor = None
        self._catalog_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = (item.title, item.relative_path, item.purpose, item.kind)
            for column, value in enumerate(values):
                self._catalog_table.setItem(row, column, QTableWidgetItem(value))
        self._entry_table.setRowCount(0)
        self._load_more.setEnabled(False)
        self._tune_button.setEnabled(False)
        if not schema_id:
            self._scheme_note.setText("未识别当前启用方案；不会混合显示其他已安装词库。")
            self._status.setText("请先在 Rime 中启用白霜拼音或万象拼音方案。")
        else:
            self._scheme_note.setText(
                "仅显示该方案实际引用的词典；系统词典和学习库保持只读。"
            )
            self._status.setText(
                f"已识别 {len(items)} 个词典文件或入口，请选择一个词典。"
            )

    def _on_scheme_changed(self, index: int) -> None:
        if index != self._scheme_index and not self._confirm_discard_editable():
            self._scheme.blockSignals(True)
            try:
                self._scheme.setCurrentIndex(self._scheme_index)
            finally:
                self._scheme.blockSignals(False)
            return
        self._scheme_index = index
        self._refresh_catalog()

    def _on_catalog_selection(self) -> None:
        row = self._catalog_table.currentRow()
        if row != self._catalog_row and not self._confirm_discard_editable():
            self._catalog_table.blockSignals(True)
            try:
                if self._catalog_row >= 0:
                    self._catalog_table.selectRow(self._catalog_row)
                else:
                    self._catalog_table.clearSelection()
            finally:
                self._catalog_table.blockSignals(False)
            return
        self._catalog_row = row
        self._selected_item = self._catalog[row] if 0 <= row < len(self._catalog) else None
        self._leave_editable_mode()
        self._search_timer.stop()
        self._next_cursor = None
        self._load_more.setEnabled(False)
        self._entry_table.setRowCount(0)
        if self._selected_item is None:
            return
        if not self._selected_item.entry_paths:
            self._status.setText(
                "Rime 学习词库可能被输入法进程锁定，为避免损坏仅显示状态，不读取或编辑。"
            )
            self._update_tune_state()
            return
        if self._can_direct_edit(self._selected_item):
            self._load_editable_dictionary(self._selected_item)
            return
        self._load_entries()

    def _schedule_entries(self, _text: str = "") -> None:
        if self._selected_item is not None:
            if self._edit_session is not None:
                self._show_editable_entries()
                return
            self._next_cursor = None
            self._search_timer.start()

    def _load_entries(self, append: bool = False) -> None:
        item = self._selected_item
        if item is None or not item.entry_paths:
            return
        if append and self._next_cursor is None:
            return
        self._request_id += 1
        request_id = self._request_id
        query = self._search.text()
        self._loading_append = append
        self._load_more.setEnabled(False)
        self._status.setText("正在读取下一页…" if append else "正在读取词典…")

        if self._data_engine_client is not None:
            payload = {
                "rime_dir": self._rime_dir,
                "paths": [str(path) for path in item.entry_paths],
                "query": query,
                "limit": 200,
                "cursor": self._next_cursor if append else None,
            }

            def deliver(result, error) -> None:
                self._results.put((
                    request_id, item.relative_path, result, error, append,
                ))

            self._data_engine_client.request_latest(
                "dictionary_entries_page", payload, deliver,
                channel=self._engine_channel,
            )
            return

        def worker() -> None:
            rows = self._service.read_entries(item, query=query, limit=500)
            self._results.put((
                request_id, item.relative_path,
                {"entries": rows, "next_cursor": None}, None, append,
            ))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_results(self) -> None:
        while True:
            try:
                request_id, relative_path, result, error, append = self._results.get_nowait()
            except queue.Empty:
                return
            if request_id != self._request_id:
                continue
            if self._selected_item is None \
                    or self._selected_item.relative_path != relative_path:
                continue
            if error is not None:
                self._status.setText(f"读取词典失败：{error}")
                self._status.setProperty("role", "error")
                continue
            result = result or {}
            self._show_entries(
                list(result.get("entries") or []),
                append=append,
                next_cursor=result.get("next_cursor"),
            )

    def _show_entries(
        self,
        rows: list[DictionaryEntry | dict],
        *,
        append: bool = False,
        next_cursor: dict | None = None,
    ) -> None:
        offset = self._entry_table.rowCount() if append else 0
        self._entry_table.setRowCount(offset + len(rows))
        for relative_row, entry in enumerate(rows):
            row = offset + relative_row
            if isinstance(entry, dict):
                values = (
                    str(entry.get("text", "")), str(entry.get("code", "")),
                    str(entry.get("weight") or "—"),
                    str(entry.get("source_file", "")),
                )
            else:
                values = (entry.text, entry.code, entry.weight or "—", entry.source_file)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, values[1])
                self._entry_table.setItem(row, column, cell)
        self._next_cursor = next_cursor
        self._load_more.setEnabled(next_cursor is not None)
        self._status.setText(
            f"已显示 {self._entry_table.rowCount()} 条精确结果；"
            "双击或点击按钮可创建个人覆盖调整。"
        )
        self._update_tune_state()

    def _update_tune_state(self) -> None:
        self._tune_button.setEnabled(
            self._edit_session is None
            and self._tune_entry is not None and self._entry_table.currentRow() >= 0
        )
        if self._edit_session is not None:
            self._delete_button.setEnabled(self._entry_table.currentRow() >= 0)

    def _tune_selected(self) -> None:
        if self._edit_session is not None:
            return
        row = self._entry_table.currentRow()
        if row < 0 or self._tune_entry is None:
            return
        text_item = self._entry_table.item(row, 0)
        code_item = self._entry_table.item(row, 1)
        if text_item is None or code_item is None:
            return
        self._tune_entry(text_item.text(), input_code(code_item.text()))

    @staticmethod
    def _can_direct_edit(item: DictionaryCatalogItem) -> bool:
        return DictionaryEditSession.can_edit(item)

    def _load_editable_dictionary(self, item: DictionaryCatalogItem) -> None:
        self._edit_session = DictionaryEditSession(
            item, self._transaction_writer, self._backup_service,
        )
        self._entry_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        for button in (
            self._add_button, self._delete_button,
            self._undo_button, self._save_button,
        ):
            button.show()
        self._undo_button.setEnabled(False)
        self._save_button.setEnabled(False)
        self._tune_button.hide()
        self._load_more.hide()
        self._show_editable_entries()
        self._status.setText(
            f"正在编辑 {item.relative_path}；修改先暂存，点击保存后统一写入。"
        )

    def _leave_editable_mode(self) -> None:
        self._edit_session = None
        self._entry_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for button in (
            getattr(self, "_add_button", None),
            getattr(self, "_delete_button", None),
            getattr(self, "_undo_button", None),
            getattr(self, "_save_button", None),
        ):
            if button is not None:
                button.hide()
        if hasattr(self, "_tune_button"):
            self._tune_button.show()
        if hasattr(self, "_load_more"):
            self._load_more.show()

    def _show_editable_entries(self) -> None:
        session = self._edit_session
        if session is None:
            return
        visible = session.visible_entries(self._search.text())
        self._rebuilding_entries = True
        try:
            self._entry_table.setRowCount(len(visible))
            source = self._selected_item.relative_path if self._selected_item else ""
            for row, (entry_index, entry) in enumerate(visible):
                values = (entry.text, entry.code, str(entry.weight), source)
                for column, value in enumerate(values):
                    cell = QTableWidgetItem(value)
                    cell.setData(Qt.ItemDataRole.UserRole, entry_index)
                    if column == 3:
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._entry_table.setItem(row, column, cell)
        finally:
            self._rebuilding_entries = False
        self._update_tune_state()

    def _on_editable_item_changed(self, item: QTableWidgetItem) -> None:
        session = self._edit_session
        if self._rebuilding_entries or session is None:
            return
        entry_index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry_index, int):
            return
        previous = (
            str(session.entries[entry_index].weight)
            if 0 <= entry_index < len(session.entries) else ""
        )
        error = session.update(entry_index, item.column(), item.text())
        if error:
            if item.column() == 2:
                self._rebuilding_entries = True
                try:
                    item.setText(previous)
                finally:
                    self._rebuilding_entries = False
            self._status.setText(error)
            self._status.setProperty("role", "error")
            return
        self._show_editable_dirty()

    def _show_editable_dirty(self) -> None:
        self._save_button.setEnabled(True)
        self._undo_button.setEnabled(True)
        self._status.setText("● 有未保存的词典修改。")

    def _add_editable_entry(self) -> None:
        session = self._edit_session
        if session is None:
            return
        self._search.clear()
        session.add()
        self._show_editable_dirty()
        self._show_editable_entries()
        row = self._entry_table.rowCount() - 1
        if row >= 0:
            self._entry_table.selectRow(row)
            self._entry_table.editItem(self._entry_table.item(row, 0))

    def _delete_editable_entry(self) -> None:
        row = self._entry_table.currentRow()
        item = self._entry_table.item(row, 0) if row >= 0 else None
        entry_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        session = self._edit_session
        if session is None or not isinstance(entry_index, int):
            return
        if session.delete(entry_index):
            self._show_editable_dirty()
            self._show_editable_entries()

    def _undo_editable_changes(self) -> None:
        item = self._selected_item
        if item is not None and self._can_direct_edit(item):
            self._load_editable_dictionary(item)
            self._status.setText("已撤销未保存的词典修改。")

    def _save_editable_changes(self) -> None:
        session = self._edit_session
        item = self._selected_item
        if session is None or item is None or not session.dirty:
            return
        try:
            count = session.save()
        except ValueError as exc:
            self._status.setText(str(exc))
            self._status.setProperty("role", "error")
            return
        except Exception as exc:
            self._status.setText(f"保存失败，暂存修改已保留：{exc}")
            self._status.setProperty("role", "error")
            return
        self._save_button.setEnabled(False)
        self._undo_button.setEnabled(False)
        self._status.setProperty("role", "success")
        self._status.setText(f"已保存 {count} 条：{item.relative_path}")
        if self._saved_callback is not None:
            self._saved_callback(str(item.path))

    def _confirm_discard_editable(self) -> bool:
        if not self._editable_dirty:
            return True
        answer = QMessageBox.question(
            self, "放弃未保存修改？",
            "当前词典修改尚未保存，继续将放弃这些修改。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def done(self, result: int) -> None:
        if not self._confirm_discard_editable():
            return
        self.dispose()
        super().done(result)

    def dispose(self) -> None:
        """Stop background work when a modal closes or an embedded page is replaced."""
        if self._data_engine_client is not None:
            self._data_engine_client.cancel_pending(self._engine_channel)
        self._search_timer.stop()
        self._poll_timer.stop()
