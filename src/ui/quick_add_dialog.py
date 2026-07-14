"""快速收藏弹窗（QuickAddDialog）。

热键收藏 / 托盘入口触发：预填剪贴板文本。
- 编码：自动填入无声调全拼，也可直接手动修改；
- 采集失败时保留弹窗并提示用户手动输入；
- 回车直接关闭（保存）；窗口始终置顶。
权重：
- 默认 1；下拉 1 / 2 / 3 / 自定义…；
- 选『自定义…』弹出整数输入框（1~99），不再内联数字框；
- 确认后显示为『自定义（N）』；取消则恢复选择前的权重；
- 弹窗尺寸稳定，不随输入弹窗跳动。
"""
from __future__ import annotations

from typing import List, Optional
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.service.pinyin_service import PinyinService
from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import PhraseRepo
from src.ui.click_activated_combo import ClickActivatedComboBox
from src.ui.encoding_suggestion_panel import EncodingSuggestionPanel
from src.ui.rime_preview_panel import RimePreviewPanel


class QuickAddDialog(QDialog):
    """快速收藏单条词条。"""

    def __init__(self, prefill_text: str = "",
                 groups: Optional[List[str]] = None,
                 pinyin: PinyinService | None = None,
                 repo: PhraseRepo | None = None,
                 system_dictionary_index=None,
                 rime_preview_service=None,
                 create_group=None,
                 notice: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("收藏到词库")
        self._groups = list(groups or [])
        self._pinyin = pinyin or PinyinService()
        self._repo = repo
        self._system_dictionary_index = system_dictionary_index
        self._rime_preview_service = rime_preview_service
        self._create_group_callback = create_group
        self._recommended_weight: Optional[int] = None
        self._custom_weight: Optional[int] = None  # 选择自定义后的具体权重
        self._prev_index = 0                        # 进入自定义前的下拉索引
        self._suppress = False                      # 防止程序化回退触发二次弹窗
        self._additional_codes: list[str] = []
        self._build_ui()
        # 冲突区按需展开，窗口保留可缩放能力。
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setFixedWidth(680)

        # 5 秒倒计时自动关闭（默认关闭；保留 _tick 代码，将 _auto_close 改为
        # True 即可恢复。待最终测试完成后再决定是否启用自动退出。）
        self._auto_close = False
        self._remaining = 5
        self._timer = QTimer(self)
        self._dictionary_timer = QTimer(self)
        self._dictionary_timer.setInterval(450)
        self._dictionary_timer.timeout.connect(self._refresh_dictionary_hint)
        self._timer.timeout.connect(self._tick)
        if prefill_text:
            self._text.setText(prefill_text)
            self._update_code()
        if notice:
            self.set_notice(notice)
        if self._auto_close:
            self._timer.start(1000)
            self._tick()
        else:
            self._countdown.hide()

        # 焦点：有捕获文本 → 编码（便于校正）；无捕获 → 文本框
        if prefill_text:
            self._code.setFocus()
            self._code.selectAll()
        else:
            self._text.setFocus()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._preview_panel = RimePreviewPanel(
            self._rime_preview_service, self._system_dictionary_index, self._repo, self,
        )
        layout.addWidget(self._preview_panel)
        form = QFormLayout()
        form.setSpacing(10)

        self._text = QLineEdit()
        self._text.setPlaceholderText("选中文本（自动预填剪贴板）")
        self._text.textEdited.connect(self._update_code)

        self._code = QLineEdit()
        self._code.setPlaceholderText("可点击建议或手填；弯引号和反引号会自动更正")
        self._code.textEdited.connect(self._normalize_code_input)
        self._btn_separator = QPushButton("'")
        self._btn_separator.setFixedWidth(34)
        self._btn_separator.setToolTip("在光标处插入显示分隔符（不会写入词库编码）")
        self._btn_separator.clicked.connect(self._insert_separator)
        self._btn_add_code = QPushButton("增加编码")
        self._btn_add_code.setMinimumWidth(82)
        self._btn_add_code.clicked.connect(self._add_current_code)
        code_row = QHBoxLayout()
        code_row.addWidget(self._code, 1)
        code_row.addWidget(self._btn_separator)
        code_row.addWidget(self._btn_add_code)
        self._extra_code_box = QWidget()
        self._extra_code_layout = QVBoxLayout(self._extra_code_box)
        self._extra_code_layout.setContentsMargins(0, 4, 0, 0)
        self._extra_code_layout.setSpacing(4)
        self._extra_code_box.hide()
        code_container = QWidget()
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(0, 0, 0, 0)
        code_container_layout.setSpacing(0)
        code_container_layout.addLayout(code_row)
        code_container_layout.addWidget(self._extra_code_box)

        # 权重：下拉 1/2/3/自定义…，不再内联数字框
        self._weight_combo = ClickActivatedComboBox()
        for weight in (1, 2, 3):
            self._weight_combo.addItem(str(weight), weight)
        self._weight_combo.addItem("自定义…", None)
        self._weight_combo.setCurrentIndex(0)  # 默认权重 1
        self._weight_combo.currentIndexChanged.connect(self._on_weight_mode_changed)

        form.addRow("文本：", self._text)
        form.addRow("编码：", code_container)
        form.addRow("权重：", self._weight_combo)

        self._group = ClickActivatedComboBox()
        self._group.addItem("（无）", "")
        for g in self._groups:
            self._group.addItem(g, g)
        self._btn_new_group = QPushButton("新建分组")
        self._btn_new_group.setFixedWidth(82)
        self._btn_new_group.clicked.connect(self._create_group)
        group_row = QHBoxLayout()
        group_row.addWidget(self._group, 1)
        group_row.addWidget(self._btn_new_group)
        form.addRow("分组：", group_row)
        self._english_upper = QCheckBox("英文输出为大写（编码保持小写）")
        self._english_upper.setEnabled(False)
        form.addRow("英文：", self._english_upper)
        layout.addLayout(form)

        self._suggestion_panel = EncodingSuggestionPanel(
            self._pinyin, self._repo, self
        )
        self._suggestion_panel.codeSelected.connect(self._set_suggested_code)
        self._suggestion_panel.codeAddRequested.connect(self._add_code)
        layout.addWidget(self._suggestion_panel)
        self._dictionary_hint = QLabel("")
        self._dictionary_hint.setWordWrap(True)
        self._dictionary_hint.setProperty("role", "info")
        self._btn_apply_dictionary_weight = QPushButton("采用建议权重")
        self._btn_apply_dictionary_weight.clicked.connect(self._apply_dictionary_weight)
        self._btn_apply_dictionary_weight.hide()
        dictionary_row = QHBoxLayout()
        dictionary_row.addWidget(self._dictionary_hint, 1)
        dictionary_row.addWidget(self._btn_apply_dictionary_weight)
        self._dictionary_hint_box = QWidget()
        self._dictionary_hint_box.setLayout(dictionary_row)
        self._dictionary_hint_box.hide()
        layout.addWidget(self._dictionary_hint_box)
        # 提示（热键未捕获时按错误色显示，保证足够醒目）
        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setProperty("role", "error")
        self._info.hide()
        layout.addWidget(self._info)

        # 错误（红色，仅用于校验失败）
        self._err = QLabel("")
        self._err.setWordWrap(True)
        self._err.setProperty("role", "error")
        layout.addWidget(self._err)

        self._countdown = QLabel("")
        self._countdown.setProperty("role", "info")
        layout.addWidget(self._countdown)

        # 中文按钮：收藏 / 取消
        buttons = QDialogButtonBox()
        btn_ok = buttons.addButton("收藏", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.setDefault(True)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    def set_notice(self, text: str) -> None:
        """无捕获等提示：使用错误色，避免与皮肤色混在一起。"""
        self._info.setProperty("role", "error")
        self._info.style().unpolish(self._info)
        self._info.style().polish(self._info)
        self._info.setText(text)
        self._info.show()

    def _update_code(self) -> None:
        """文本变化时刷新建议，并默认选用带边界的全拼。"""
        text = self._text.text().strip()
        self._update_english_output_option(text)
        self._suggestion_panel.set_text(text)
        if text:
            suggestions = build_suggestions(text, self._pinyin)
            self._code.setText(
                suggestions[0].display_code if suggestions
                else self._pinyin.get_full_pinyin(text)
            )
            self._suggestion_panel.select_code(self._code.text())
            self._err.setText("")
        else:
            self._code.clear()
        self._refresh_dictionary_hint()
        self._request_rime_preview()

    def _update_english_output_option(self, text: str) -> None:
        enabled = bool(re.fullmatch(r"[A-Za-z][A-Za-z -]*", (text or "").strip()))
        self._english_upper.setEnabled(enabled)
        if not enabled:
            self._english_upper.setChecked(False)

    def _stored_text_and_display_code(self) -> tuple[str, str]:
        text = self._text.text().strip()
        display = normalize_display_code(self._code.text())
        if self._english_upper.isChecked():
            text = text.upper()
            display = display.lower()
        return text, display

    def _add_current_code(self) -> None:
        self._add_code(self._code.text())

    def _add_code(self, code: str) -> None:
        display = normalize_display_code(code)
        if not raw_code(display):
            self._err.setText("请先填写可用编码。")
            return
        if display not in self._additional_codes:
            self._additional_codes.append(display)
        self._rebuild_extra_codes()

    def _remove_code(self, display: str) -> None:
        self._additional_codes = [item for item in self._additional_codes if item != display]
        self._rebuild_extra_codes()

    def _rebuild_extra_codes(self) -> None:
        while self._extra_code_layout.count():
            item = self._extra_code_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, display in enumerate(self._additional_codes, start=1):
            row = QWidget(self._extra_code_box)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            label = QLabel(f"编码{index}")
            label.setObjectName("ExtraCodeName")
            value = QLineEdit(display)
            value.setReadOnly(True)
            remove = QPushButton("删除")
            remove.setObjectName("SuggestionAdd")
            remove.clicked.connect(lambda _=False, code=display: self._remove_code(code))
            row_layout.addWidget(label)
            row_layout.addWidget(value, 1)
            row_layout.addWidget(remove)
            self._extra_code_layout.addWidget(row)
        self._extra_code_box.setVisible(bool(self._additional_codes))
        self._suggestion_panel.set_excluded_codes(self._additional_codes)
        self._refresh_dictionary_hint()
        self._request_rime_preview()

    def _set_suggested_code(self, code: str) -> None:
        self._code.setText(code)
        self._code.setFocus()
        self._code.setCursorPosition(len(code))
        self._refresh_dictionary_hint()
        self._request_rime_preview()

    def _normalize_code_input(self, value: str) -> None:
        normalized = normalize_display_code(value)
        if normalized != value:
            position = self._code.cursorPosition()
            self._code.blockSignals(True)
            self._code.setText(normalized)
            self._code.setCursorPosition(min(position, len(normalized)))
            self._code.blockSignals(False)
        self._suggestion_panel.select_code(normalized)
        self._refresh_dictionary_hint()
        self._request_rime_preview()

    def _request_rime_preview(self) -> None:
        self._preview_panel.set_query(self._text.text(), self._code.text())

    def _create_group(self) -> None:
        if self._create_group_callback is None:
            self._err.setText("当前词库未启用分组。")
            return
        name, accepted = QInputDialog.getText(self, "新建分组", "分组名称：")
        name = name.strip()
        if not accepted or not name:
            return
        if name not in self._groups and not self._create_group_callback(name):
            self._err.setText("分组创建失败或名称已存在。")
            return
        if name not in self._groups:
            self._groups.append(name)
            self._group.addItem(name, name)
        self._group.setCurrentIndex(self._group.findData(name))
        self._err.setText("")

    def _refresh_dictionary_hint(self) -> None:
        index = self._system_dictionary_index
        text = self._text.text().strip()
        codes = [self._code.text()] + list(self._additional_codes)
        if index is None or not text:
            self._dictionary_timer.stop()
            self._dictionary_hint_box.hide()
            return
        index.ensure_ready_async()
        state = index.state
        if state == "building":
            self._recommended_weight = None
            self._dictionary_hint.setText("系统词典索引准备中，不影响当前收藏。")
            self._dictionary_hint_box.show()
            self._btn_apply_dictionary_weight.hide()
            if not self._dictionary_timer.isActive():
                self._dictionary_timer.start()
            return
        self._dictionary_timer.stop()
        if state != "ready":
            self._dictionary_hint_box.hide()
            return
        candidates = index.lookup(text, codes)
        if not candidates:
            self._recommended_weight = None
            self._dictionary_hint_box.hide()
            return
        labels = []
        weights = []
        for item in candidates[:3]:
            detail = f"{item.source}（本工具参考优先级 {item.quality:g}"
            if item.weight is not None:
                detail += f"，原始权重 {item.weight}"
                weights.append(item.weight)
            detail += "）"
            if detail not in labels:
                labels.append(detail)
        self._recommended_weight = min(99, max([self._weight_value(), *weights]) + 1) if weights else None
        suffix = (f" 建议自定义权重 {self._recommended_weight}。"
                  if self._recommended_weight else " 可按需要调整自定义权重。")
        self._dictionary_hint.setText(
            "系统词典已有同码候选：" + "、".join(labels) + "。" + suffix
            + " 本工具参考优先级仅用于整理提示，不参与 Rime 实际排序。"
        )
        self._dictionary_hint_box.show()
        self._btn_apply_dictionary_weight.setVisible(self._recommended_weight is not None)

    def _apply_dictionary_weight(self) -> None:
        if self._recommended_weight is None:
            return
        self._custom_weight = self._recommended_weight
        self._suppress = True
        self._weight_combo.setCurrentIndex(3)
        self._weight_combo.setItemText(3, f"自定义（{self._recommended_weight}）")
        self._prev_index = 3
        self._suppress = False
        self._refresh_dictionary_hint()

    def _insert_separator(self) -> None:
        value = self._code.text()
        position = self._code.cursorPosition()
        normalized = normalize_display_code(
            value[:position] + "'" + value[position:]
        )
        self._code.setText(normalized)
        self._code.setFocus()
        self._code.setCursorPosition(min(position + 1, len(normalized)))
        self._refresh_dictionary_hint()
        self._request_rime_preview()

    def _on_weight_mode_changed(self) -> None:
        if self._suppress:
            return
        # 选中『自定义…』（data 为 None）→ 弹出整数输入框
        if self._weight_combo.currentData() is None:
            prev = self._prev_index
            val, ok = self._get_int_input(self._custom_weight or 1)
            if ok:
                self._custom_weight = val
                self._suppress = True
                self._weight_combo.setItemText(
                    self._weight_combo.currentIndex(), f"自定义（{val}）")
                self._prev_index = self._weight_combo.currentIndex()
                self._suppress = False
            else:
                # 取消输入：恢复选择前的权重（不改动窗口尺寸）
                self._suppress = True
                self._weight_combo.setCurrentIndex(prev)
                self._suppress = False
        else:
            self._prev_index = self._weight_combo.currentIndex()

    def _get_int_input(self, default: int) -> tuple[int, bool]:
        from PySide6.QtWidgets import QInputDialog

        val, ok = QInputDialog.getInt(
            self, "自定义权重", "输入权重（1 ~ 99）：", default, 1, 99)
        return val, ok

    def _weight_value(self) -> int:
        value = self._weight_combo.currentData()
        if value is not None:
            return int(value)
        if self._custom_weight is not None:
            return self._custom_weight
        return 1

    def _tick(self) -> None:
        if self._remaining <= 0:
            self._timer.stop()
            if self._text.text().strip():
                self.accept()  # 倒计时结束：保存收藏
            else:
                self.reject()  # 文本为空：仅关闭
            return
        self._countdown.setText(f"{self._remaining} 秒后自动关闭并收藏")
        self._remaining -= 1

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._on_accept()
            return
        super().keyPressEvent(event)

    def _on_accept(self) -> None:
        if not self._text.text().strip():
            self._err.setText("文本不能为空。")
            return
        self._err.setText("")
        self.accept()

    # ------------------------------------------------------------------ #
    def get_values(self) -> dict:
        stored_text, display_code = self._stored_text_and_display_code()
        return {
            "text": stored_text,
            "code": raw_code(display_code),
            "display_code": display_code,
            "weight": self._weight_value(),
            "group": self._group.currentData() or "",
            "weight_updates": self._suggestion_panel.weight_updates(),
            "additional_codes": list(self._additional_codes),
        }
