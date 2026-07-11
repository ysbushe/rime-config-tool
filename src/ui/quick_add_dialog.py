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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.service.pinyin_service import PinyinService
from src.encoding.code_suggestions import build_suggestions, normalize_display_code, raw_code
from src.repo.phrase_repo import PhraseRepo
from src.ui.encoding_suggestion_panel import EncodingSuggestionPanel


class QuickAddDialog(QDialog):
    """快速收藏单条词条。"""

    def __init__(self, prefill_text: str = "",
                 groups: Optional[List[str]] = None,
                 pinyin: PinyinService | None = None,
                 repo: PhraseRepo | None = None,
                 notice: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("收藏到词库")
        self._groups = list(groups or [])
        self._pinyin = pinyin or PinyinService()
        self._repo = repo
        self._custom_weight: Optional[int] = None  # 选择自定义后的具体权重
        self._prev_index = 0                        # 进入自定义前的下拉索引
        self._suppress = False                      # 防止程序化回退触发二次弹窗
        self._build_ui()
        # 冲突区按需展开，窗口保留可缩放能力。
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumWidth(620)

        # 5 秒倒计时自动关闭（默认关闭；保留 _tick 代码，将 _auto_close 改为
        # True 即可恢复。待最终测试完成后再决定是否启用自动退出。）
        self._auto_close = False
        self._remaining = 5
        self._timer = QTimer(self)
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
        code_row = QHBoxLayout()
        code_row.addWidget(self._code, 1)
        code_row.addWidget(self._btn_separator)

        # 权重：下拉 1/2/3/自定义…，不再内联数字框
        self._weight_combo = QComboBox()
        for weight in (1, 2, 3):
            self._weight_combo.addItem(str(weight), weight)
        self._weight_combo.addItem("自定义…", None)
        self._weight_combo.setCurrentIndex(0)  # 默认权重 1
        self._weight_combo.currentIndexChanged.connect(self._on_weight_mode_changed)

        form.addRow("文本：", self._text)
        form.addRow("编码：", code_row)
        form.addRow("权重：", self._weight_combo)

        self._group = QComboBox()
        self._group.addItem("（无）", "")
        for g in self._groups:
            self._group.addItem(g, g)
        form.addRow("分组：", self._group)
        layout.addLayout(form)

        self._suggestion_panel = EncodingSuggestionPanel(
            self._pinyin, self._repo, self
        )
        self._suggestion_panel.codeSelected.connect(self._set_suggested_code)
        layout.addWidget(self._suggestion_panel)

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

    def _set_suggested_code(self, code: str) -> None:
        self._code.setText(code)
        self._code.setFocus()
        self._code.setCursorPosition(len(code))

    def _normalize_code_input(self, value: str) -> None:
        normalized = normalize_display_code(value)
        if normalized != value:
            position = self._code.cursorPosition()
            self._code.blockSignals(True)
            self._code.setText(normalized)
            self._code.setCursorPosition(min(position, len(normalized)))
            self._code.blockSignals(False)
        self._suggestion_panel.select_code(normalized)

    def _insert_separator(self) -> None:
        value = self._code.text()
        position = self._code.cursorPosition()
        normalized = normalize_display_code(
            value[:position] + "'" + value[position:]
        )
        self._code.setText(normalized)
        self._code.setFocus()
        self._code.setCursorPosition(min(position + 1, len(normalized)))

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
        display_code = normalize_display_code(self._code.text())
        return {
            "text": self._text.text().strip(),
            "code": raw_code(display_code),
            "display_code": display_code,
            "weight": self._weight_value(),
            "group": self._group.currentData() or "",
            "weight_updates": self._suggestion_panel.weight_updates(),
        }
