"""Shared Rime candidate-preview strip for collection and editing dialogs."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.encoding.code_suggestions import raw_code
from src.ui.theme import accent_color


class RimePreviewPanel(QWidget):
    """Show a compact input-method-style candidate row and its weights."""

    def __init__(self, preview_service=None, dictionary_index=None, repo=None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RimePreviewPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._service = preview_service
        self._dictionary_index = dictionary_index
        self._repo = repo
        self._text = ""
        self._code = ""
        # A preview can refresh several times while the engine reports state changes.
        # Cache the active code's managed weights instead of scanning the whole library each time.
        self._managed_code = ""
        self._managed_weights: dict[str, int] = {}

        self._status = QLabel("")
        self._status.setObjectName("RimePreviewStatus")
        self._status.setWordWrap(True)
        self._candidate_row = QWidget(self)
        self._weight_row = QWidget(self)
        self._candidate_layout = QHBoxLayout(self._candidate_row)
        self._weight_layout = QHBoxLayout(self._weight_row)
        for layout in (self._candidate_layout, self._weight_layout):
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._candidate_title = QLabel("候选：")
        self._candidate_title.setObjectName("RimePreviewWeightTitle")
        self._candidate_title.setFixedWidth(58)
        self._candidate_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._weight_title = QLabel("权重：")
        self._weight_title.setObjectName("RimePreviewWeightTitle")
        self._weight_title.setFixedWidth(58)
        self._weight_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._candidate_layout.addWidget(self._candidate_title)
        self._weight_layout.addWidget(self._weight_title)

        self._candidate_cells: list[QLabel] = []
        self._weight_cells: list[QLabel] = []
        for _ in range(5):
            candidate = QLabel("")
            candidate.setObjectName("RimePreviewCandidate")
            candidate.setTextFormat(Qt.TextFormat.RichText)
            candidate.setFixedWidth(105)
            candidate.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            weight = QLabel("")
            weight.setObjectName("RimePreviewWeight")
            weight.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            weight.setFixedWidth(105)
            weight.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self._candidate_layout.addWidget(candidate)
            self._weight_layout.addWidget(weight)
            candidate.hide()
            weight.hide()
            self._candidate_cells.append(candidate)
            self._weight_cells.append(weight)

        self._details = QLabel("")
        self._details.setObjectName("RimePreviewDetails")
        self._details.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(2)
        layout.addWidget(self._status)
        layout.addWidget(self._candidate_row)
        layout.addWidget(self._weight_row)
        layout.addWidget(self._details)
        self._candidate_row.hide()
        self._weight_row.hide()
        # Reserve the final two-row candidate area from the first paint onward.
        self.setFixedHeight(90)
        self.hide()

        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(120)
        self._request_timer.timeout.connect(self._begin_request)
        if self._service is not None and hasattr(self._service, "events"):
            self._service.events.changed.connect(self._refresh)

    def set_query(self, text: str, code: str) -> None:
        self._text = (text or "").strip()
        next_code = raw_code(code)
        if next_code != self._code:
            self._managed_code = ""
            self._managed_weights = {}
        self._code = next_code
        if self._service is None or not self._code:
            self._request_timer.stop()
            self.hide()
            return
        self._show_status("输入法候选预览：正在读取…", "候选与权重将在读取完成后显示。")
        self._request_timer.start()

    def _begin_request(self) -> None:
        if self._service is None or not self._code:
            return
        if self._dictionary_index is not None:
            self._dictionary_index.ensure_ready_async()
        self._service.request(self._code)
        self._refresh()


    def _show_status(self, title: str, detail: str, stop: bool = False) -> None:
        self._candidate_row.hide()
        self._weight_row.hide()
        self._status.setText(title)
        self._status.show()
        self._details.setText(detail)
        self.show()


    def _refresh(self) -> None:
        if self._service is None or not self._code:
            self.hide()
            return
        snapshot = self._service.snapshot
        if snapshot.code and snapshot.code != self._code:
            return
        if snapshot.state in {"loading", "querying", "stale"}:
            self._show_status("输入法候选预览：正在加载 Rime 引擎…", "独立预览不影响正在使用的输入法。")
            return
        if snapshot.state == "waiting_deploy":
            self._show_status("输入法候选预览：词库修改尚未部署。", "完成部署后将按最新配置刷新候选。", stop=True)
            return
        if snapshot.state == "unavailable":
            self._show_status("输入法候选预览暂不可用。", snapshot.message, stop=True)
            return
        if snapshot.state != "ready":
            return
        if not snapshot.candidates:
            self._show_status("输入法候选预览：未找到可显示候选。", "", stop=True)
            return
        self._show_candidates(snapshot.candidates[:5])

    def _show_candidates(self, candidates) -> None:
        indexed = {}
        if self._dictionary_index is not None:
            for item in self._dictionary_index.lookup_code(self._code, limit=80):
                indexed.setdefault(item.text, item)
        if self._managed_code != self._code:
            managed: dict[str, int] = {}
            if self._repo is not None:
                for phrase in self._repo.all():
                    if phrase.code == self._code:
                        managed[phrase.text] = max(managed.get(phrase.text, 0), phrase.weight)
            self._managed_code = self._code
            self._managed_weights = managed
        managed = self._managed_weights

        accent = accent_color()
        current_rank = None
        for index, candidate_label in enumerate(self._candidate_cells):
            weight_label = self._weight_cells[index]
            if index >= len(candidates):
                candidate_label.hide()
                weight_label.hide()
                continue
            candidate = candidates[index]
            text = escape(candidate.text)
            if candidate.text == self._text:
                current_rank = index + 1
                text = f'<span style="color:{accent}; font-weight:600">{text}</span>'
            candidate_label.setText(f"{index + 1}. {text}")
            if candidate.text in managed:
                weight = str(managed[candidate.text])
                weight_label.setToolTip("自定义词库权重")
            elif candidate.text in indexed and indexed[candidate.text].weight is not None:
                weight = str(indexed[candidate.text].weight)
                weight_label.setToolTip("系统词典原始权重")
            else:
                weight = "—"
                weight_label.setToolTip("未找到可显示的静态权重")
            weight_label.setText(weight)
            candidate_label.show()
            weight_label.show()

        self._status.hide()
        self._candidate_row.show()
        self._weight_row.show()
        detail = "权重说明：同一词典内数值越大通常越靠前；不同词典、用户词频和过滤器仍会影响最终顺序。"
        if current_rank is not None:
            detail += f" 当前采集文本位于第 {current_rank} 候选。"
        self._details.setText(detail)
        self.show()
