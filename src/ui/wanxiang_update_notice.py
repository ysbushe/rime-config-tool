"""Plain-language Wanxiang update status shared by startup and Settings."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QLabel,
    QVBoxLayout, QWidget,
)


def friendly_reason(reason: str) -> str:
    text = str(reason or "")
    if "可谨慎更新" in text or "能力检查通过" in text:
        return "新版未登记，但自动检查通过；需要您确认后更新"
    if "已通过结构预检" in text:
        return "新版已通过结构和设置检查，可以更新"
    if "高于" in text or "版本" in text and "验证" in text:
        return "新版尚未通过本工具的兼容验证"
    if "目录" in text or "结构" in text:
        return "新版文件结构发生变化，需要先更新适配"
    if "下载" in text or "校验" in text:
        return "新版的下载或校验方式发生变化"
    if "配置" in text or "映射" in text or "补丁" in text:
        return "新版配置暂时无法安全转换为可视化设置"
    return text or "新版需要先完成兼容验证"


class WanxiangStatusPanel(QFrame):
    """Vertical, readable status; colour supplements rather than replaces text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WanxiangStatusPanel")
        form = QFormLayout(self)
        form.setContentsMargins(12, 10, 12, 10)
        form.setSpacing(7)
        self.current = QLabel("尚未检查", self)
        self.available = QLabel("尚未检查", self)
        self.tool = QLabel("—", self)
        self.result = QLabel("等待检查", self)
        self.result.setWordWrap(True)
        form.addRow("当前已安装：", self.current)
        form.addRow("发现新版本：", self.available)
        form.addRow("本工具版本：", self.tool)
        form.addRow("处理结果：", self.result)

    def set_report(self, report) -> None:
        self.current.setText(report.current_version or "未识别")
        self.available.setText(report.new_version or "未发现")
        self.tool.setText(report.tool_version or "未知")
        self.result.setText(friendly_reason(report.reason))
        role = "warning" if (
            report.waiting_for_adapter
            or bool(getattr(report, "requires_confirmation", False))
        ) else (
            "success" if report.compatible else "error"
        )
        self.result.setProperty("role", role)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def set_message(self, message: str, role: str = "info") -> None:
        self.result.setText(message)
        self.result.setProperty("role", role)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)


class WanxiangCompatibilityDialog(QDialog):
    LATER = 1
    DISMISS = 2

    def __init__(self, report, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("发现新的万象版本")
        self.resize(520, 380)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        title = QLabel("当前万象可以继续正常使用", self)
        title.setObjectName("DialogTitle")
        intro = QLabel(
            "程序会先检查新版的文件结构和现有设置是否仍能对应。"
            "未登记但检查通过的版本可由您确认后更新；检查失败时不会写入任何文件。",
            self,
        )
        intro.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(intro)
        panel = WanxiangStatusPanel(self)
        panel.set_report(report)
        root.addWidget(panel)
        explanation = QLabel(
            "更新保护方式\n"
            "版本号只用于提示，不再直接阻止更新。程序会实际检查新版内容；"
            "安装前另存一份独立恢复备份，个人词库和自定义设置始终不会被更新包覆盖。",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setProperty("role", "info")
        root.addWidget(explanation)
        root.addStretch(1)
        buttons = QDialogButtonBox(self)
        later = buttons.addButton("稍后提醒", QDialogButtonBox.ButtonRole.ActionRole)
        dismiss = buttons.addButton("暂不更新", QDialogButtonBox.ButtonRole.RejectRole)
        close = buttons.addButton("关闭说明", QDialogButtonBox.ButtonRole.AcceptRole)
        later.clicked.connect(lambda: self.done(self.LATER))
        dismiss.clicked.connect(lambda: self.done(self.DISMISS))
        close.clicked.connect(self.accept)
        root.addWidget(buttons)
