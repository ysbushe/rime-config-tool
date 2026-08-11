"""Focused visual editors for input habits and Weasel candidate appearance."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.ui.skin_config_widget import SkinConfigWidget


class _PatchPage(QWidget):
    saved = Signal(str)
    filename = ""

    def __init__(self, title: str, subtitle: str, service_provider,
                 environment_provider, deploy_request=None, parent=None) -> None:
        super().__init__(parent)
        self._service_provider = service_provider
        self._environment_provider = environment_provider
        self._deploy_request = deploy_request
        self._revision = None
        self._loaded_scheme_id = ""
        self._loaded = False
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(12)
        heading = QLabel(title, self)
        heading.setObjectName("PageTitle")
        note = QLabel(subtitle, self)
        note.setObjectName("PageSubtitle")
        note.setWordWrap(True)
        self._context = QLabel(self)
        self._context.setProperty("role", "info")
        root.addWidget(heading)
        root.addWidget(note)
        root.addWidget(self._context)
        self._content = QVBoxLayout()
        root.addLayout(self._content, 1)
        actions = QHBoxLayout()
        self._status = QLabel(self)
        self._status.setWordWrap(True)
        self._save = QPushButton("保存并应用", self)
        self._save.setObjectName("Primary")
        self._save.clicked.connect(self._save_changes)
        actions.addWidget(self._status, 1)
        actions.addWidget(self._save)
        root.addLayout(actions)

    def activate(self) -> None:
        resolution = self._environment_provider()
        name = resolution.files.display_name if resolution.files else "未确认方案"
        self._context.setText(f"当前方案：{name}　｜　修改保存后才会应用")
        self._context.setToolTip(f"保存位置：{self.filename}")
        self._save.setEnabled(bool(resolution.writable))
        if not resolution.writable:
            self._status.setText("当前方案不可安全确认，本页保持只读：" + resolution.reason)
            return
        if self._loaded and str(resolution.schema_id or "") == self._loaded_scheme_id:
            return
        self._load()

    def _load(self) -> None:
        service = self._service_provider()
        try:
            data, self._revision = service.read(self.filename)
            self._load_values(dict(data.get("patch") or {}))
            resolution = self._environment_provider()
            self._loaded_scheme_id = str(resolution.schema_id or "")
            self._loaded = True
            self._status.setText("修改会暂存在页面中，点击保存后才写入。")
        except Exception as exc:
            self._save.setEnabled(False)
            self._status.setText(str(exc))

    def _save_changes(self) -> None:
        if not self._loaded or self._revision is None:
            return
        resolution = self._environment_provider()
        if not resolution.writable:
            QMessageBox.warning(self, "方案已变化", "当前方案无法确认，未写入任何文件。")
            return
        if str(resolution.schema_id or "") != self._loaded_scheme_id:
            QMessageBox.warning(
                self, "方案已变化",
                "输入方案已切换，本页原有内容不会写入新方案。请重新进入本页后再修改。",
            )
            self.activate()
            return
        try:
            path = self._service_provider().save_updates(
                self.filename, self._updates(), self._revision,
            )
            self._status.setText("设置已保存，正在应用到当前输入方案。")
            self._status.setToolTip(f"已保存：{path.name}")
            self._load()
            if self._deploy_request is not None:
                self._deploy_request()
            self.saved.emit(str(path))
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def _load_values(self, patch: dict) -> None:
        raise NotImplementedError

    def _updates(self) -> dict:
        raise NotImplementedError


def _help_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setProperty("role", "muted")
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def _explained_control(control: QWidget, text: str, parent: QWidget,
                       *, control_width: int = 220) -> tuple[QWidget, QLabel]:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(14)
    control.setMinimumWidth(control_width)
    control.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    help_text = _help_label(text, row)
    layout.addWidget(control)
    layout.addWidget(help_text, 1)
    return row, help_text


class InputHabitsWidget(_PatchPage):
    filename = "rime_frost.custom.yaml"

    def __init__(self, service_provider, environment_provider,
                 deploy_request=None, parent=None) -> None:
        super().__init__(
            "输入习惯",
            "集中管理全拼方案共用的候选数量、可并存的翻页键与中英文切换；不提供裸 Ctrl 选词。",
            service_provider, environment_provider, deploy_request, parent,
        )
        common = QGroupBox("通用全拼设置", self)
        form = QFormLayout(common)
        self._page_size = QSpinBox(common)
        self._page_size.setRange(1, 10)
        paging = QWidget(common)
        paging_layout = QHBoxLayout(paging)
        paging_layout.setContentsMargins(0, 0, 0, 0)
        paging_layout.setSpacing(30)
        self._paging_brackets = QCheckBox("[ 与 ]", paging)
        self._paging_minus = QCheckBox("- 与 =", paging)
        self._paging_comma = QCheckBox(", 与 .", paging)
        for checkbox in (
            self._paging_brackets, self._paging_minus, self._paging_comma,
        ):
            checkbox.setMinimumWidth(92)
        paging_layout.addWidget(self._paging_brackets)
        paging_layout.addWidget(self._paging_minus)
        paging_layout.addWidget(self._paging_comma)
        paging_layout.addStretch(1)
        selection = QWidget(common)
        selection_layout = QHBoxLayout(selection)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        selection_layout.setSpacing(30)
        self._select_space = QCheckBox("空格提交当前高亮候选", selection)
        self._select_space.setChecked(True)
        self._select_space.setEnabled(False)
        self._select_extra = QCheckBox("分号选第 2、单引号选第 3", selection)
        selection_layout.addWidget(self._select_space)
        selection_layout.addWidget(self._select_extra)
        selection_layout.addStretch(1)
        self._shift = QComboBox(common)
        self._shift.addItem("提交编码并切换英文", "commit_code")
        self._shift.addItem("临时英文输入", "inline_ascii")
        self._shift.addItem("不处理", "noop")
        shift_row, self._shift_help = _explained_control(
            self._shift,
            "按下左 Shift 时，先确认当前拼音，再切换到英文输入。",
            common,
        )
        form.addRow("每页候选数量：", self._page_size)
        form.addRow("翻页按键（可多选）：", paging)
        form.addRow("候选上屏：", selection)
        form.addRow("左 Shift：", shift_row)
        self._content.addWidget(common)
        self._wanxiang_group = QGroupBox("万象专用设置", self)
        wanxiang = self._wanxiang_group
        wanxiang_layout = QVBoxLayout(wanxiang)
        info = QLabel(
            "这些设置只影响万象拼音。暂未列出的高级功能由程序自动保护，"
            "一般使用时不需要手动处理配置文件。",
            wanxiang,
        )
        info.setWordWrap(True)
        info.setProperty("role", "warning")
        wanxiang_layout.addWidget(info)
        wanxiang_form = QFormLayout()
        self._wx_user_dict = QCheckBox("启用用户词频与个人词库", wanxiang)
        self._wx_sentence = QCheckBox("启用整句组句", wanxiang)
        self._wx_context = QCheckBox("启用上下文建议", wanxiang)
        self._wx_english_spacing = QComboBox(wanxiang)
        for title, value in (
            ("智能处理", "smart"), ("英文前留空格", "before"),
            ("英文后留空格", "after"), ("不自动留空格", "off"),
        ):
            self._wx_english_spacing.addItem(title, value)
        self._wx_english_count = QSpinBox(wanxiang)
        self._wx_english_count.setRange(1, 9)
        self._wx_tone_lookup = QCheckBox("反查时允许声调", wanxiang)
        user_dict_row, _ = _explained_control(
            self._wx_user_dict, "记住你的常用词和使用频率，让常用候选逐渐靠前。",
            wanxiang, control_width=210,
        )
        sentence_row, _ = _explained_control(
            self._wx_sentence, "输入较长拼音时，允许自动组合成完整句子。",
            wanxiang, control_width=210,
        )
        context_row, _ = _explained_control(
            self._wx_context, "参考前面已经输入的内容，调整接下来候选词的顺序。",
            wanxiang, control_width=210,
        )
        spacing_row, self._wx_spacing_help = _explained_control(
            self._wx_english_spacing, "根据中英文相邻情况自动决定是否添加空格。",
            wanxiang,
        )
        count_row, _ = _explained_control(
            self._wx_english_count, "英文候选最多显示多少项；数量越少，候选窗越简洁。",
            wanxiang,
        )
        tone_row, _ = _explained_control(
            self._wx_tone_lookup, "用拼音查字时可以输入声调，以缩小查找范围。",
            wanxiang, control_width=210,
        )
        wanxiang_form.addRow(user_dict_row)
        wanxiang_form.addRow(sentence_row)
        wanxiang_form.addRow(context_row)
        wanxiang_form.addRow("英文空格：", spacing_row)
        wanxiang_form.addRow("英文候选数量：", count_row)
        wanxiang_form.addRow(tone_row)
        wanxiang_layout.addLayout(wanxiang_form)
        self._content.addWidget(wanxiang)
        self._content.addStretch(1)
        self._preserved_bindings: list[dict] = []
        self._shift.currentIndexChanged.connect(self._refresh_input_help)
        self._wx_english_spacing.currentIndexChanged.connect(self._refresh_input_help)
        self._refresh_input_help()

    def _refresh_input_help(self, *_args) -> None:
        shift_help = {
            "commit_code": "先确认当前拼音，再切换到英文输入。",
            "inline_ascii": "按住左 Shift 时临时输入英文，松开后继续中文输入。",
            "noop": "左 Shift 只作为普通按键，不改变中英文状态。",
        }
        spacing_help = {
            "smart": "根据中英文相邻情况自动决定是否添加空格。",
            "before": "在英文内容前面自动添加一个空格。",
            "after": "在英文内容后面自动添加一个空格。",
            "off": "不自动添加空格，完全按输入内容输出。",
        }
        self._shift_help.setText(shift_help.get(str(self._shift.currentData()), ""))
        self._wx_spacing_help.setText(
            spacing_help.get(str(self._wx_english_spacing.currentData()), "")
        )

    def activate(self) -> None:
        resolution = self._environment_provider()
        self.filename = (
            "wanxiang.custom.yaml" if str(resolution.schema_id or "") == "wanxiang"
            else "rime_frost.custom.yaml"
        )
        self._wanxiang_group.setEnabled(str(resolution.schema_id or "") == "wanxiang")
        super().activate()

    def _load_values(self, patch: dict) -> None:
        self._page_size.setValue(int(patch.get("menu/page_size", 5)))
        bindings = patch.get("key_binder/bindings/+", [])
        bindings = bindings if isinstance(bindings, list) else []
        accepts = {
            str(item.get("accept", ""))
            for item in bindings if isinstance(item, dict)
        }
        self._paging_brackets.setChecked("bracketleft" in accepts or "bracketright" in accepts)
        self._paging_minus.setChecked("minus" in accepts or "equal" in accepts)
        self._paging_comma.setChecked("comma" in accepts or "period" in accepts)
        self._select_extra.setChecked("semicolon" in accepts or "apostrophe" in accepts)
        managed = {
            "bracketleft", "bracketright", "minus", "equal", "comma", "period",
            "semicolon", "apostrophe",
        }
        self._preserved_bindings = [
            dict(item) for item in bindings
            if isinstance(item, dict) and str(item.get("accept", "")) not in managed
        ]
        shift = str(patch.get("ascii_composer/switch_key/Shift_L", "commit_code"))
        self._shift.setCurrentIndex(max(0, self._shift.findData(shift)))
        self._wx_user_dict.setChecked(bool(patch.get("translator/enable_user_dict", True)))
        self._wx_sentence.setChecked(bool(patch.get("translator/enable_sentence", True)))
        self._wx_context.setChecked(bool(patch.get("translator/contextual_suggestions", True)))
        spacing = str(patch.get("wanxiang_english/english_spacing", "smart"))
        self._wx_english_spacing.setCurrentIndex(
            max(0, self._wx_english_spacing.findData(spacing))
        )
        self._wx_english_count.setValue(int(patch.get("wanxiang_english/max_candidates", 5)))
        self._wx_tone_lookup.setChecked(bool(patch.get("wanxiang_lookup/enable_tone", True)))

    def _updates(self) -> dict:
        bindings = list(self._preserved_bindings)
        if self._paging_brackets.isChecked():
            bindings.extend([
                {"when": "paging", "accept": "bracketleft", "send": "Page_Up"},
                {"when": "has_menu", "accept": "bracketright", "send": "Page_Down"},
            ])
        if self._paging_minus.isChecked():
            bindings.extend([
                {"when": "paging", "accept": "minus", "send": "Page_Up"},
                {"when": "has_menu", "accept": "equal", "send": "Page_Down"},
            ])
        if self._paging_comma.isChecked():
            bindings.extend([
                {"when": "paging", "accept": "comma", "send": "Page_Up"},
                {"when": "has_menu", "accept": "period", "send": "Page_Down"},
            ])
        if self._select_extra.isChecked():
            bindings.extend([
                {"when": "has_menu", "accept": "semicolon", "send": "2"},
                {"when": "has_menu", "accept": "apostrophe", "send": "3"},
            ])
        updates = {
            "menu/page_size": self._page_size.value(),
            "key_binder/bindings/+": bindings,
            "ascii_composer/switch_key/Shift_L": self._shift.currentData(),
        }
        if self.filename == "wanxiang.custom.yaml":
            updates.update({
                "translator/enable_user_dict": self._wx_user_dict.isChecked(),
                "translator/enable_sentence": self._wx_sentence.isChecked(),
                "translator/contextual_suggestions": self._wx_context.isChecked(),
                "wanxiang_english/english_spacing": self._wx_english_spacing.currentData(),
                "wanxiang_english/max_candidates": self._wx_english_count.value(),
                "wanxiang_lookup/enable_tone": self._wx_tone_lookup.isChecked(),
            })
        return updates


CandidateAppearanceWidget = SkinConfigWidget


class LibraryMaintenanceWidget(QWidget):
    def __init__(self, phrase_manager, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        title = QLabel("词库维护", self)
        title.setObjectName("PageTitle")
        note = QLabel("集中放置低频、批量和诊断工具；日常增删改仍在词库工作台完成。", self)
        note.setObjectName("PageSubtitle")
        root.addWidget(title)
        root.addWidget(note)
        group = QGroupBox("维护工具", self)
        form = QFormLayout(group)
        actions = (
            ("重复项管理", "查找同文本多编码与同编码多文本", phrase_manager.open_duplicates),
            ("词库健康检查", "检查空值、权重、重复项和无效分组", phrase_manager.open_health_check),
            ("自动分组", "按现有规则预览并整理未分组词条", phrase_manager.run_auto_group),
            ("跨方案导入", "对白霜与万象个人词库执行安全导入", phrase_manager.open_scheme_import),
            ("部署状态", "查看部署进度、最近结果并可立即重新部署", phrase_manager.open_deployment_status),
            ("修改历史", "查看本工具记录的词库操作历史", phrase_manager.open_history),
        )
        for title_text, detail, callback in actions:
            button = QPushButton(title_text, group)
            button.clicked.connect(callback)
            form.addRow(QLabel(detail, group), button)
        root.addWidget(group)
        root.addStretch(1)
