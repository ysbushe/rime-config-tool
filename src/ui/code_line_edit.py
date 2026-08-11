"""Qt editor that locally disables IME composition for Rime code fields."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QKeySequence, QRegularExpressionValidator
from PySide6.QtWidgets import QApplication, QLineEdit, QStyledItemDelegate, QToolTip

from src.encoding.code_input import allowed_code_characters, sanitize_code_input


def _validator_pattern(alphabet: str, legacy_characters: str = "") -> str:
    allowed = set(allowed_code_characters(alphabet))
    allowed.update(
        char for char in legacy_characters
        if char.isascii() and char.isprintable() and not char.isspace()
    )
    escaped = QRegularExpression.escape("".join(sorted(allowed)))
    return f"^[{escaped}]*$"


class CodeLineEdit(QLineEdit):
    """A code-only editor; it never changes the process-wide input method."""

    inputRejected = Signal(str)

    def __init__(self, text: str = "", parent=None, *, alphabet: str = "") -> None:
        super().__init__(parent)
        self._code_alphabet = alphabet
        self._last_rejected = ""
        self._legacy_characters = text
        self.setInputMethodHints(
            Qt.InputMethodHint.ImhLatinOnly
            | Qt.InputMethodHint.ImhNoPredictiveText
            | Qt.InputMethodHint.ImhNoAutoUppercase
        )
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(_validator_pattern(alphabet)), self,
            )
        )
        self.setToolTip("编码仅接受当前方案允许的编码按键；中文、全角字符和空白会被屏蔽。")
        if text:
            self.setValidator(
                QRegularExpressionValidator(
                    QRegularExpression(_validator_pattern(alphabet, text)), self,
                )
            )
            self.setText(text)

    @property
    def code_alphabet(self) -> str:
        return self._code_alphabet

    @property
    def last_rejected(self) -> str:
        return self._last_rejected

    def _report_rejected(self, rejected: str) -> None:
        if not rejected:
            return
        self._last_rejected = rejected
        self.inputRejected.emit(rejected)
        shown = " ".join(repr(char) for char in rejected)
        QToolTip.showText(
            self.mapToGlobal(self.rect().bottomLeft()),
            f"编码不支持：{shown}",
            self,
        )

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.StandardKey.Paste):
            accepted, rejected = sanitize_code_input(
                QApplication.clipboard().text(), self._code_alphabet,
            )
            if accepted:
                self.insert(accepted)
                self.textEdited.emit(self.text())
            self._report_rejected(rejected)
            event.accept()
            return
        text = event.text()
        if text and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            accepted, rejected = sanitize_code_input(text, self._code_alphabet)
            self._report_rejected(rejected)
            if not accepted:
                event.accept()
                return
            if accepted != text:
                replacement = QKeyEvent(
                    event.type(), event.key(), event.modifiers(), accepted,
                    event.isAutoRepeat(), event.count(),
                )
                super().keyPressEvent(replacement)
                event.accept()
                return
        super().keyPressEvent(event)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        commit = event.commitString()
        if commit:
            accepted, rejected = sanitize_code_input(commit, self._code_alphabet)
            self._report_rejected(rejected)
            event.setCommitString(
                accepted, event.replacementStart(), event.replacementLength(),
            )
        super().inputMethodEvent(event)


class CodeItemDelegate(QStyledItemDelegate):
    """Use :class:`CodeLineEdit` for editable table code columns."""

    def __init__(self, alphabet: str = "", parent=None) -> None:
        super().__init__(parent)
        self._alphabet = alphabet

    def createEditor(self, parent, option, index):
        return CodeLineEdit(parent=parent, alphabet=self._alphabet)
