"""Friendly, safe phrase migration dialog for managed full-pinyin schemes."""
from __future__ import annotations

from collections import Counter
import queue
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.service.wanxiang_import import (
    ImportExecutionError,
    ImportConflict,
    ImportPlan,
    ImportResult,
    PhraseFormatError,
    DirectSchemeImportService, WanxiangImportService,
)
from src.ui.dialog_workbench import dialog_section


class ImportConflictDialog(QDialog):
    """Choose the source or target version for each differing existing row."""

    def __init__(self, conflicts: list[ImportConflict], selected: set[tuple[str, str]],
                 source_name: str, target_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self.setWindowTitle("逐项处理已有差异")
        self.resize(860, 520)
        self._conflicts = list(conflicts)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        note = QLabel(
            "这些词条的文字和编码相同，但排序值或行格式不同。"
            f"可逐项保留 {target_name} 当前内容，或采用 {source_name} 的内容。",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("role", "info")
        layout.addWidget(note)
        self._table = QTableWidget(len(conflicts), 5, self)
        self._table.setHorizontalHeaderLabels([
            "字词", "编码", f"{source_name}内容", f"{target_name}内容", "本次选择",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self._table.horizontalHeader()
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        for row, conflict in enumerate(conflicts):
            for column, value in enumerate((
                conflict.source.text, conflict.source.code,
                conflict.source.raw, conflict.target.raw,
            )):
                self._table.setItem(row, column, QTableWidgetItem(value))
            choice = QComboBox(self._table)
            choice.addItem(f"保留 {target_name}", False)
            choice.addItem(f"采用 {source_name}", True)
            choice.setCurrentIndex(1 if conflict.source.key in selected else 0)
            self._table.setCellWidget(row, 4, choice)
        layout.addWidget(self._table, 1)
        actions = QHBoxLayout()
        cancel = QPushButton("取消", self)
        cancel.clicked.connect(self.reject)
        apply = QPushButton("应用选择", self)
        apply.setObjectName("Primary")
        apply.clicked.connect(self.accept)
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        layout.addLayout(actions)

    def overwrite_keys(self) -> set[tuple[str, str]]:
        result = set()
        for row, conflict in enumerate(self._conflicts):
            choice = self._table.cellWidget(row, 4)
            if isinstance(choice, QComboBox) and bool(choice.currentData()):
                result.add(conflict.source.key)
        return result


class WanxiangImportDialog(QDialog):
    """Preview and run a safe copy into the currently active scheme."""

    importCompleted = Signal(str)

    def __init__(self, service: WanxiangImportService | DirectSchemeImportService, *, sandbox_mode: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AppDialog")
        self._source_name = service.source_name
        self._target_name = service.target_name
        self.setWindowTitle("跨方案导入个人词库")
        self.resize(700, 570)
        self._service = service
        self._sandbox_mode = bool(sandbox_mode)
        self._plan: ImportPlan | None = None
        self._running = False
        self._results: queue.SimpleQueue[tuple[bool, object]] = queue.SimpleQueue()
        self._overwrite_keys: set[tuple[str, str]] = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        overview, overview_layout = dialog_section(self, "导入方向")
        flow = QLabel(f"{self._source_name}个人词库  →  {self._target_name}个人词库", overview)
        flow.setObjectName("ImportFlow")
        flow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overview_layout.addWidget(flow)
        intro = QLabel(
            f"来源 {self._source_name} 词库不会被修改；只把目标中没有的词条复制到"
            f" {self._target_name}。如果导入失败，会按安全规则恢复。",
            overview,
        )
        intro.setWordWrap(True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        overview_layout.addWidget(intro)
        root.addWidget(overview)

        stats = QWidget(self)
        stats.setObjectName("ImportStats")
        stats_layout = QHBoxLayout(stats)
        stats_layout.setContentsMargins(12, 8, 12, 8)
        self._source_value = self._add_stat(stats_layout, "来源词条总数")
        self._import_value = self._add_stat(stats_layout, "目标将新增")
        self._duplicate_value = self._add_stat(stats_layout, "目标已有并跳过")
        self._warning_value = self._add_stat(stats_layout, "需确认的新增项")
        root.addWidget(stats)

        self._conflict_button = QPushButton("逐项处理已有差异", self)
        self._conflict_button.setVisible(False)
        self._conflict_button.clicked.connect(self._choose_conflicts)
        root.addWidget(self._conflict_button)

        notice, notice_layout = dialog_section(self, "检查结果")
        self._notice = QLabel("正在检查，不会写入文件。", notice)
        self._notice.setWordWrap(True)
        notice_layout.addWidget(self._notice)
        root.addWidget(notice)

        self._warning_confirm = QCheckBox("", self)
        self._warning_confirm.setObjectName("ImportWarningConfirmation")
        self._warning_confirm.setVisible(False)
        self._warning_confirm.toggled.connect(self._update_execute_enabled)
        root.addWidget(self._warning_confirm)

        self._details_toggle = QToolButton(self)
        self._details_toggle.setText("详细信息（排查问题时使用）")
        self._details_toggle.setCheckable(True)
        self._details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._details_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._details_toggle.toggled.connect(self._toggle_details)
        root.addWidget(self._details_toggle)

        self._technical = QPlainTextEdit(self)
        self._technical.setReadOnly(True)
        self._technical.setMaximumHeight(155)
        self._technical.setVisible(False)
        self._technical.setPlaceholderText("文件位置和校验步骤会显示在这里。")
        self._report = self._technical  # compatibility for existing UI tests
        root.addWidget(self._technical)

        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        actions = QHBoxLayout()
        self._scan_button = QPushButton("重新检查", self)
        self._scan_button.clicked.connect(self.scan)
        self._execute_button = QPushButton("开始导入", self)
        self._execute_button.setObjectName("Primary")
        self._execute_button.clicked.connect(self._execute)
        self._close_button = QPushButton("关闭", self)
        self._close_button.clicked.connect(self.reject)
        actions.addWidget(self._scan_button)
        actions.addWidget(self._execute_button)
        actions.addStretch(1)
        actions.addWidget(self._close_button)
        root.addLayout(actions)

        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain_result)
        self._initial_scan_timer = QTimer(self)
        self._initial_scan_timer.setSingleShot(True)
        self._initial_scan_timer.timeout.connect(self.scan)
        self._initial_scan_timer.start(0)
        self._disposed = False

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._initial_scan_timer.stop()
        self._poll.stop()

    def done(self, result: int) -> None:
        self.dispose()
        super().done(result)

    @staticmethod
    def _add_stat(layout: QHBoxLayout, caption: str) -> QLabel:
        column = QVBoxLayout()
        value = QLabel("—")
        value.setObjectName("ImportStatValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(caption)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(value)
        column.addWidget(label)
        layout.addLayout(column, 1)
        return value

    def _toggle_details(self, visible: bool) -> None:
        self._details_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )
        self._technical.setVisible(visible)

    def _set_status(self, text: str, role: str) -> None:
        self._status.setText(text)
        self._status.setProperty("role", role)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _set_notice(self, text: str, role: str) -> None:
        self._notice.setText(text)
        self._notice.setProperty("role", role)
        self._notice.style().unpolish(self._notice)
        self._notice.style().polish(self._notice)

    def _reset_counts(self) -> None:
        for label in (
            self._source_value, self._import_value,
            self._duplicate_value, self._warning_value,
        ):
            label.setText("—")

    def scan(self) -> None:
        if self._disposed or self._running:
            return
        self._plan = None
        self._reset_counts()
        self._warning_confirm.setChecked(False)
        self._warning_confirm.setVisible(False)
        self._execute_button.setEnabled(False)
        self._set_notice(f"正在检查 {self._source_name} 与 {self._target_name} 个人词库……", "info")
        self._set_status("检查只读取文件，不会执行导入。", "neutral")
        try:
            plan = self._service.plan(self._overwrite_keys)
        except PhraseFormatError as exc:
            self._technical.setPlainText(str(exc))
            self._set_notice(self._friendly_scan_error(str(exc)), "error")
            self._set_status("无法继续，未修改任何文件。", "error")
            return
        except Exception as exc:
            self._technical.setPlainText(str(exc))
            self._set_notice("检查没有完成，请展开详细信息查看原因。", "error")
            self._set_status("未修改任何文件。", "error")
            return

        self._plan = plan
        warning_entries = self._warning_entry_count(plan)
        self._source_value.setText(str(plan.source_count))
        self._import_value.setText(str(plan.import_count))
        self._duplicate_value.setText(str(
            plan.skipped_exact + plan.skipped_different
        ))
        self._warning_value.setText(str(warning_entries))
        self._technical.setPlainText(self._format_technical(plan))
        self._conflict_button.setVisible(bool(plan.conflicts))
        if plan.conflicts:
            self._conflict_button.setText(
                f"逐项处理已有差异（{len(self._overwrite_keys)}/"
                f"{len(plan.conflicts)} 项采用来源）"
            )

        if plan.blockers:
            self._set_notice(self._friendly_blockers(plan.blockers), "error")
            self._set_status("为了避免覆盖或丢失内容，程序不会继续。", "error")
        elif self._sandbox_mode:
            self._set_notice("检查完成；当前处于沙盒模式。", "warning")
            self._set_status("沙盒模式只能查看结果，不能修改真实词库。", "warning")
        elif plan.import_count == 0:
            if plan.skipped_different:
                self._set_notice(
                    f"没有新词条；其中 {plan.skipped_different} 条排序值或格式不同，"
                    f"已保留{self._target_name}词库中的现有内容。",
                    "info",
                )
            else:
                self._set_notice(f"{self._target_name}词库已经包含这些内容，无需重复导入。", "info")
            self._set_status("没有需要写入的新词条。", "neutral")
        elif plan.warnings:
            warning_text = self._friendly_warnings(plan)
            if plan.skipped_different:
                warning_text += (
                    f"\n另有 {plan.skipped_different} 条已存在但排序值或格式不同；"
                    f"本次保留{self._target_name}词库中的现有内容。"
                )
            self._set_notice(warning_text, "warning")
            self._warning_confirm.setText(
                f"我知道这 {warning_entries} 条需要注意的内容会保持原样复制"
            )
            self._warning_confirm.setVisible(True)
            self._set_status("请确认上面的提示，然后即可开始导入。", "warning")
        elif plan.skipped_different:
            self._set_notice(
                f"检查完成；{plan.skipped_different} 条已存在内容的排序值或格式不同，"
                f"本次保留{self._target_name}词库中的现有内容。",
                "info",
            )
            self._set_status(f"可以开始导入新的词条。{self._source_name}词库不会被修改。", "success")
        else:
            self._set_notice("检查完成，没有发现需要人工确认的问题。", "success")
            self._set_status(f"可以开始导入。{self._source_name}词库不会被修改。", "success")
        self._update_execute_enabled()

    def _choose_conflicts(self) -> None:
        plan = self._plan
        if plan is None or not plan.conflicts:
            return
        dialog = ImportConflictDialog(
            plan.conflicts, self._overwrite_keys,
            self._source_name, self._target_name, self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._overwrite_keys = dialog.overwrite_keys()
        self.scan()

    @staticmethod
    def _warning_entry_count(plan: ImportPlan) -> int:
        return len({warning.line_number for warning in plan.warnings})

    @staticmethod
    def _friendly_scan_error(message: str) -> str:
        if "wanxiang.schema.yaml" in message:
            return "没有找到万象拼音配置。请先安装万象拼音，并完成一次重新部署。"
        if ".dict.yaml" in message:
            return "选中的内容是固定词典，不是个人短语词库，本功能不能导入。"
        if "CSV" in message:
            return "这个文件不是 Rime 的 Tab 分隔个人词库，本功能不能导入。"
        if "UTF-16" in message or "UTF-8" in message:
            return "词库文字编码不受支持，请先将文件保存为 UTF-8。"
        return "词库格式无法安全识别，请展开详细信息查看原因。"

    @staticmethod
    def _friendly_blockers(blockers: list[str]) -> str:
        message = "；".join(blockers)
        if "已使用自定义词库" in message:
            return (
                "万象正在使用另一份个人词库。为避免覆盖现有设置，"
                "本工具不会自动替换它。"
            )
        if "无法识别的行" in message:
            return "来源词库中有无法安全识别的内容。为避免漏词，本工具不会跳过后继续。"
        if "没有可导入" in message:
            return "来源个人词库中没有可导入的词条。"
        if "user_dict" in message:
            return "万象配置中没有找到个人词库入口，请先重新部署或检查安装。"
        return "当前配置存在冲突。为避免覆盖内容，本工具不会继续。"

    @classmethod
    def _friendly_warnings(cls, plan: ImportPlan) -> str:
        counts = Counter(warning.kind for warning in plan.warnings)
        lines: list[str] = []
        if counts.get("special_code"):
            lines.append(
                f"有 {counts['special_code']} 条编码不是普通全拼，可能含数字或符号；"
                "程序会原样保留。"
            )
        if counts.get("pinyin_delimiter"):
            lines.append(
                f"有 {counts['pinyin_delimiter']} 条拼音带音节分隔符（例如 xi'an）；"
                "程序会原样保留。"
            )
        if counts.get("dynamic_text"):
            lines.append(
                f"有 {counts['dynamic_text']} 条内容可能被万象识别为特殊格式，"
                "导入后的显示方式可能不同。"
            )
        if counts.get("non_numeric_weight"):
            lines.append(
                f"有 {counts['non_numeric_weight']} 条排序值格式特殊，"
                "万象可能忽略这些排序值。"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_technical(plan: ImportPlan) -> str:
        counts = Counter(warning.kind for warning in plan.warnings)
        lines = [
            f"来源文件：{plan.source_path}",
            f"目标文件：{plan.target_path}",
            f"来源条目：{plan.source_count}",
            f"计划新增：{plan.import_count}",
            f"完全重复：{plan.skipped_exact}",
            f"同词同码但内容不同：{plan.skipped_different}",
        ]
        if plan.patch_path is not None:
            lines.insert(2, f"目标方案挂载补丁：{plan.patch_path}")
        if counts:
            lines.append("风险分类：" + "，".join(
                f"{kind}={amount}" for kind, amount in sorted(counts.items())
            ))
        if plan.blockers:
            lines.append("阻断项：" + "；".join(plan.blockers))
        lines.extend([
            "提交步骤：创建快照 → 原子写入 → 重新部署 → 验证目标文件。",
            "提交前会复检文件指纹；失败时按并发安全规则恢复。",
        ])
        return "\n".join(lines)

    def _update_execute_enabled(self) -> None:
        plan = self._plan
        enabled = bool(
            plan
            and plan.can_execute
            and not self._sandbox_mode
            and not self._running
        )
        self._execute_button.setEnabled(enabled)
        if self._running:
            self._execute_button.setText("正在导入…")
        elif self._sandbox_mode:
            self._execute_button.setText("沙盒中不可导入")
        elif plan is not None and plan.blockers:
            self._execute_button.setText("当前无法导入")
        elif plan is not None and plan.import_count == 0:
            self._execute_button.setText("无需导入")
        else:
            self._execute_button.setText("开始导入")

    def _execute(self) -> None:
        plan = self._plan
        if not plan or not plan.can_execute or self._running:
            return
        if plan.warnings and not self._warning_confirm.isChecked():
            self._warning_confirm.setFocus(Qt.FocusReason.TabFocusReason)
            self._set_status("请先勾选“保持原样复制”的确认项。", "warning")
            QMessageBox.information(
                self,
                "还差一步",
                "检查发现了需要注意的内容。\n\n"
                "请先勾选提示下方的确认项，再开始导入。",
            )
            return
        answer = QMessageBox.question(
            self,
            "确认开始导入",
            f"将把 {plan.import_count} 条内容复制到{self._target_name}个人词库。\n\n"
            f"{self._source_name}词库不会被修改。是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._running = True
        self._scan_button.setEnabled(False)
        self._close_button.setEnabled(False)
        self._update_execute_enabled()
        self._set_notice(f"正在复制词库并重新部署{self._target_name}……", "info")
        self._set_status("请稍候；失败时程序会按安全规则恢复。", "info")
        self._poll.start()

        def worker() -> None:
            try:
                self._results.put((True, self._service.execute(plan)))
            except Exception as exc:
                self._results.put((False, exc))

        threading.Thread(target=worker, name="wanxiang-import", daemon=True).start()

    def _drain_result(self) -> None:
        if self._disposed:
            return
        try:
            ok, payload = self._results.get_nowait()
        except queue.Empty:
            return
        self._poll.stop()
        self._running = False
        self._scan_button.setEnabled(True)
        self._close_button.setEnabled(True)
        if ok:
            result = payload
            assert isinstance(result, ImportResult)
            message = (
                f"导入完成：新增 {result.imported} 条，已存在并跳过 "
                f"{result.skipped_exact + result.skipped_different} 条。"
            )
            if result.skipped_different:
                message += (
                    f" 其中 {result.skipped_different} 条排序值或格式不同，"
                    "已保留万象原有内容。"
                )
            self._set_notice(f"导入完成，{self._target_name}已经重新部署并验证成功。", "success")
            self._set_status(message, "success")
            self._technical.appendPlainText(f"\n备份位置：{result.snapshot_dir}")
            self._execute_button.setEnabled(False)
            self.importCompleted.emit(message)
            QMessageBox.information(
                self, "导入完成",
                f"{message}\n\n{self._target_name}已经重新部署并验证成功。",
            )
        else:
            error = payload
            if not isinstance(error, ImportExecutionError):
                error = ImportExecutionError(str(error))
            self._technical.appendPlainText(f"\n执行错误：{error}")
            self._set_notice("导入没有完成，请展开详细信息查看原因。", "error")
            self._set_status(
                "程序已按安全规则处理；如提示外部修改，请使用备份人工核对。",
                "error",
            )
            self._execute_button.setEnabled(False)
            QMessageBox.critical(
                self, "导入未完成",
                "导入没有完成。程序已按安全规则处理原文件。\n\n"
                "请展开详细信息查看具体原因，再重新检查。",
            )

    def reject(self) -> None:
        if self._running:
            QMessageBox.information(self, "正在导入", "请等待复制和部署完成。")
            return
        super().reject()
