"""Full Qt Widgets editor for Weasel skins and candidate appearance."""
from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QCheckBox, QColorDialog, QComboBox, QFileDialog,
    QFontComboBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from src.service.weasel_skin_service import (
    SkinSchemeDraft, WeaselSkinService, hex_to_rime_color, rime_color_to_hex,
)


_COLOR_GROUPS = (
    ("窗口整体", (
        ("back_color", "窗口背景"), ("border_color", "窗口边框"),
        ("shadow_color", "窗口阴影"), ("text_color", "输入提示文字"),
        ("label_color", "序号文字"), ("candidate_text_color", "候选文字"),
        ("comment_text_color", "注释文字"),
    )),
    ("输入提示区", (
        ("hilited_text_color", "高亮文字"), ("hilited_back_color", "高亮背景"),
        ("hilited_shadow_color", "高亮阴影"),
    )),
    ("普通候选", (
        ("candidate_back_color", "候选背景"),
        ("candidate_border_color", "候选边框"),
        ("candidate_shadow_color", "候选阴影"),
    )),
    ("选中候选", (
        ("hilited_candidate_text_color", "选中文字"),
        ("hilited_candidate_back_color", "选中背景"),
        ("hilited_label_color", "选中序号"),
        ("hilited_comment_text_color", "选中注释"),
        ("hilited_candidate_border_color", "选中边框"),
        ("hilited_candidate_shadow_color", "选中阴影"),
        ("hilited_mark_color", "选中标记"),
    )),
    ("翻页箭头", (
        ("prevpage_color", "上一页"), ("nextpage_color", "下一页"),
    )),
)


def _number_combo(values: tuple[int, ...], minimum: int, maximum: int,
                  parent: QWidget) -> QComboBox:
    combo = QComboBox(parent)
    combo.setEditable(True)
    combo.addItems([str(value) for value in values])
    combo.lineEdit().setValidator(QIntValidator(minimum, maximum, combo))
    return combo


def _number_value(combo: QComboBox, default: int) -> int:
    try:
        return int(combo.currentText())
    except ValueError:
        return default


def _scroll_page(widget: QWidget, object_name: str = "") -> QScrollArea:
    scroll = QScrollArea(widget.parentWidget())
    if object_name:
        scroll.setObjectName(object_name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


def _help(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setProperty("role", "muted")
    return label


class _ColorRow(QWidget):
    changed = Signal()

    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(label, self)
        self.mode = QComboBox(self)
        self.mode.addItem("继承上级", "inherit")
        self.mode.addItem("自定义颜色", "custom")
        self.mode.addItem("恢复默认", "default")
        self.button = QPushButton("选择颜色", self)
        self.button.setMinimumWidth(118)
        self._hex = "#000000"
        layout.addWidget(self.label, 1)
        layout.addWidget(self.mode)
        layout.addWidget(self.button)
        self.mode.currentIndexChanged.connect(self._sync)
        self.button.clicked.connect(self._pick)
        self._sync()

    def _sync(self, *_args) -> None:
        custom = self.mode.currentData() == "custom"
        self.button.setEnabled(custom)
        self.button.setText(self._hex.upper() if custom else "—")
        self.button.setStyleSheet(
            f"background-color: {self._hex}; color: "
            f"{'#ffffff' if QColor(self._hex).lightness() < 120 else '#111111'};"
            if custom else ""
        )
        self.changed.emit()

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self._hex), self, "选择候选窗颜色",
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self._hex = color.name(QColor.NameFormat.HexArgb)
            # Qt returns #AARRGGBB; editor consistently presents #RRGGBBAA.
            self._hex = f"#{self._hex[3:]}{self._hex[1:3]}"
            self._sync()

    def set_rime_value(self, value, color_format: str) -> None:
        if value in (None, ""):
            self.mode.setCurrentIndex(self.mode.findData("inherit"))
        else:
            self._hex = rime_color_to_hex(value, color_format)
            self.mode.setCurrentIndex(self.mode.findData("custom"))
        self._sync()

    def rime_value(self, color_format: str):
        if self.mode.currentData() != "custom":
            return None
        return hex_to_rime_color(self._hex, color_format)


class SkinConfigWidget(QWidget):
    """Skin library, live preview and separated Weasel/schema settings."""

    saved = Signal(str)

    def __init__(self, service_provider, environment_provider,
                 deploy_request=None, parent=None) -> None:
        super().__init__(parent)
        self._service_provider = service_provider
        self._environment_provider = environment_provider
        self._deploy_request = deploy_request
        self._service: WeaselSkinService | None = None
        self._revision = None
        self._schema_revision = None
        self._loaded_scheme_id = ""
        self._draft: SkinSchemeDraft | None = None
        self._loading = False
        self._preview_raw = "wanxiang"
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 14)
        root.setSpacing(10)
        title = QLabel("候选窗外观", self)
        title.setObjectName("PageTitle")
        subtitle = QLabel(
            "管理小狼毫皮肤、字体和候选窗布局。只写个人补丁，内置与万象预设会保持只读。",
            self,
        )
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        self._context = QLabel(self)
        self._context.setProperty("role", "info")
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self._context)

        self._sections = QTabWidget(self)
        self._sections.addTab(self._build_skin_editor(), "皮肤编辑")
        self._sections.addTab(self._build_weasel_settings(), "小狼毫设置")
        self._sections.addTab(self._build_schema_appearance(), "当前方案外观")
        root.addWidget(self._sections, 1)

        action_row = QHBoxLayout()
        self._status = QLabel("修改会先留在页面中。", self)
        self._status.setWordWrap(True)
        self._reset = QPushButton("重置修改", self)
        self._save = QPushButton("保存", self)
        self._save.setObjectName("Primary")
        self._save_deploy = QPushButton("保存并重新部署", self)
        self._reset.clicked.connect(self._reload_current)
        self._save.clicked.connect(lambda: self._save_changes(False))
        self._save_deploy.clicked.connect(lambda: self._save_changes(True))
        action_row.addWidget(self._status, 1)
        action_row.addWidget(self._reset)
        action_row.addWidget(self._save)
        action_row.addWidget(self._save_deploy)
        root.addLayout(action_row)

    def _build_skin_editor(self) -> QWidget:
        page = QWidget(self)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal, page)
        layout.addWidget(splitter)

        library = QGroupBox("皮肤库", splitter)
        library_layout = QVBoxLayout(library)
        self._skin_search = QLineEdit(library)
        self._skin_search.setPlaceholderText("搜索名称或 ID")
        self._skin_list = QListWidget(library)
        self._skin_list.setAccessibleName("皮肤列表")
        library_layout.addWidget(self._skin_search)
        library_layout.addWidget(self._skin_list, 1)
        create_row = QHBoxLayout()
        self._new_skin = QPushButton("新建", library)
        self._copy_skin = QPushButton("复制", library)
        self._delete_skin = QPushButton("删除", library)
        create_row.addWidget(self._new_skin)
        create_row.addWidget(self._copy_skin)
        create_row.addWidget(self._delete_skin)
        library_layout.addLayout(create_row)
        io_row = QHBoxLayout()
        self._import_skin = QPushButton("导入", library)
        self._export_skin = QPushButton("导出", library)
        self._copy_yaml = QPushButton("复制 YAML", library)
        io_row.addWidget(self._import_skin)
        io_row.addWidget(self._export_skin)
        io_row.addWidget(self._copy_yaml)
        library_layout.addLayout(io_row)
        splitter.addWidget(library)

        editor = QWidget(splitter)
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        self._build_preview(editor_layout, editor)
        self._skin_tabs = QTabWidget(editor)
        self._skin_tabs.addTab(self._build_basic_tab(), "基础")
        self._skin_tabs.addTab(self._build_font_tab(), "字体")
        self._skin_tabs.addTab(self._build_layout_tab(), "布局")
        self._skin_tabs.addTab(self._build_color_tab(), "配色")
        editor_layout.addWidget(self._skin_tabs, 1)
        splitter.addWidget(editor)
        splitter.setSizes([250, 720])
        splitter.setStretchFactor(1, 1)

        self._skin_search.textChanged.connect(self._filter_skins)
        self._skin_list.currentItemChanged.connect(self._select_skin)
        self._new_skin.clicked.connect(self._create_skin)
        self._copy_skin.clicked.connect(self._copy_current_skin)
        self._delete_skin.clicked.connect(self._delete_current_skin)
        self._import_skin.clicked.connect(self._import_scheme)
        self._export_skin.clicked.connect(self._export_scheme)
        self._copy_yaml.clicked.connect(self._copy_scheme_yaml)
        return page

    def _build_preview(self, host: QVBoxLayout, parent: QWidget) -> None:
        preview = QGroupBox("候选窗实时预览（固定显示）", parent)
        preview.setObjectName("CandidatePreviewDock")
        preview_layout = QVBoxLayout(preview)
        header = QHBoxLayout()
        self._preview_input = QLineEdit(preview)
        self._preview_input.setText(self._preview_raw)
        self._preview_input.setClearButtonEnabled(True)
        self._preview_theme = QComboBox(preview)
        self._preview_theme.addItem("模拟浅色", "light")
        self._preview_theme.addItem("模拟深色", "dark")
        header.addWidget(self._preview_input, 1)
        header.addWidget(self._preview_theme)
        preview_layout.addLayout(header)
        self._preview_canvas = QFrame(preview)
        self._preview_canvas.setObjectName("CandidatePreviewCanvas")
        self._preview_canvas.setFixedHeight(142)
        canvas_layout = QVBoxLayout(self._preview_canvas)
        canvas_layout.setContentsMargins(9, 8, 9, 8)
        canvas_header = QHBoxLayout()
        self._preview_mode = QLabel("普通候选窗 · 横向排列", self._preview_canvas)
        self._preview_fullscreen = QLabel("全屏显示", self._preview_canvas)
        self._preview_fullscreen.setObjectName("PreviewFullscreenBadge")
        self._preview_fullscreen.hide()
        canvas_header.addWidget(self._preview_mode)
        canvas_header.addStretch(1)
        canvas_header.addWidget(self._preview_fullscreen)
        canvas_layout.addLayout(canvas_header)
        self._preview_surface = QFrame(self._preview_canvas)
        self._preview_surface.setObjectName("CandidateAppearancePreview")
        surface_layout = QVBoxLayout(self._preview_surface)
        surface_layout.setContentsMargins(12, 9, 12, 9)
        self._preview_preedit = QLabel("", self._preview_surface)
        self._preview_preedit.setObjectName("CandidatePreviewPreedit")
        self._preview_preedit.hide()
        surface_layout.addWidget(self._preview_preedit)
        self._preview_row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._preview_labels: list[QLabel] = []
        for index, text in enumerate(("1 你好", "2 输入法", "3 万象拼音", "4 配置工具")):
            label = QLabel(text, self._preview_surface)
            label.setProperty("selected", index == 0)
            self._preview_labels.append(label)
            self._preview_row.addWidget(label)
        self._preview_row.addStretch(1)
        surface_layout.addLayout(self._preview_row, 1)
        canvas_layout.addWidget(self._preview_surface, 1)
        preview_layout.addWidget(self._preview_canvas)
        host.addWidget(preview)
        self._preview_input.textEdited.connect(self._on_preview_text_edited)
        self._preview_theme.currentIndexChanged.connect(self._refresh_preview)

    def _build_basic_tab(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        info = QGroupBox("方案信息", host)
        form = QFormLayout(info)
        self._scheme_id = QLineEdit(info)
        self._scheme_id.setReadOnly(True)
        self._scheme_name = QLineEdit(info)
        self._author = QLineEdit(info)
        self._color_format = QComboBox(info)
        self._color_format.addItems(("ABGR", "ARGB", "RGBA"))
        form.addRow("皮肤 ID：", self._scheme_id)
        form.addRow("显示名称：", self._scheme_name)
        form.addRow("作者：", self._author)
        form.addRow("颜色格式：", self._color_format)
        layout.addWidget(info)
        use = QGroupBox("使用方式", host)
        use_form = QFormLayout(use)
        self._color_scheme = QComboBox(use)
        self._color_scheme_dark = QComboBox(use)
        self._follow_system = QCheckBox("跟随 Windows 深浅色自动切换", use)
        use_form.addRow("浅色模式皮肤：", self._color_scheme)
        use_form.addRow("深色模式皮肤：", self._color_scheme_dark)
        use_form.addRow(self._follow_system)
        layout.addWidget(use)
        behavior = QGroupBox("候选窗口与输入提示", host)
        behavior_form = QFormLayout(behavior)
        self._layout = QComboBox(behavior)
        for title, value in (
            ("横向", "horizontal"), ("竖向", "vertical"),
            ("文字竖排", "vertical_text"),
            ("横向全屏", "horizontal+fullscreen"),
            ("竖向全屏", "vertical+fullscreen"),
        ):
            self._layout.addItem(title, value)
        self._layout_help = _help("候选词在一行内从左到右排列。", behavior)
        layout_row = QWidget(behavior)
        row = QHBoxLayout(layout_row); row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._layout); row.addWidget(self._layout_help, 1)
        self._inline = QCheckBox("在输入位置显示未完成内容", behavior)
        self._preedit = QComboBox(behavior)
        self._preedit.addItem("显示正在输入的拼音", "composition")
        self._preedit.addItem("显示当前选中的字词", "preview")
        self._preedit.addItem("显示完整转换结果", "preview_all")
        self._preedit_help = _help("显示当前键入的拼音字母。", behavior)
        preedit_row = QWidget(behavior)
        prow = QHBoxLayout(preedit_row); prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self._preedit); prow.addWidget(self._preedit_help, 1)
        behavior_form.addRow("排列方式：", layout_row)
        behavior_form.addRow(self._inline)
        behavior_form.addRow("输入提示内容：", preedit_row)
        layout.addWidget(behavior)
        layout.addStretch(1)
        for control in (self._layout, self._inline, self._preedit,
                        self._color_scheme, self._color_scheme_dark):
            signal = getattr(control, "currentIndexChanged", None) or getattr(control, "toggled", None)
            signal.connect(self._refresh_preview)
        return _scroll_page(host, "CandidateSettingsScroll")

    def _build_font_tab(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        group = QGroupBox("常用字体", host)
        form = QFormLayout(group)
        self._font = QFontComboBox(group); self._font.setEditable(True)
        self._label_font = QFontComboBox(group); self._label_font.setEditable(True)
        self._comment_font = QFontComboBox(group); self._comment_font.setEditable(True)
        self._font_point = _number_combo((12, 13, 14, 16, 18, 20), 8, 48, group)
        self._label_font_point = _number_combo((10, 12, 14, 16), 8, 48, group)
        self._comment_font_point = _number_combo((10, 12, 14, 16), 8, 48, group)
        form.addRow("候选文字：", self._font)
        form.addRow("候选字号：", self._font_point)
        form.addRow("序号字体：", self._label_font)
        form.addRow("序号字号：", self._label_font_point)
        form.addRow("注释字体：", self._comment_font)
        form.addRow("注释字号：", self._comment_font_point)
        layout.addWidget(group)
        advanced = QGroupBox("高级字体规则", host)
        advanced.setCheckable(True); advanced.setChecked(False)
        advanced_form = QFormLayout(advanced)
        self._font_fallback = QLineEdit(advanced)
        self._font_range = QLineEdit(advanced)
        self._font_weight = QComboBox(advanced)
        self._font_weight.addItems(("正常", "中等", "粗体"))
        self._font_style = QComboBox(advanced)
        self._font_style.addItems(("正常", "斜体"))
        advanced_form.addRow("字体回退链：", self._font_fallback)
        advanced_form.addRow("Unicode 区间：", self._font_range)
        advanced_form.addRow("字重：", self._font_weight)
        advanced_form.addRow("字形：", self._font_style)
        advanced_form.addRow(_help("高级规则只在你需要为特定字符指定字体时使用。", advanced))
        layout.addWidget(advanced)
        layout.addStretch(1)
        for control in (self._font, self._font_point):
            signal = getattr(control, "currentFontChanged", None) or control.currentTextChanged
            signal.connect(self._refresh_preview)
        return _scroll_page(host)

    def _build_layout_tab(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        presets = QGroupBox("快速预设", host)
        preset_row = QHBoxLayout(presets)
        for title, name in (("紧凑横排", "compact"), ("竖向列表", "vertical"),
                            ("天圆地方", "rounded"), ("Windows 11", "win11")):
            button = QPushButton(title, presets)
            button.clicked.connect(lambda _checked=False, value=name: self._apply_preset(value))
            preset_row.addWidget(button)
        layout.addWidget(presets)
        self._layout_controls: dict[str, QComboBox] = {}
        groups = (
            ("尺寸限制", (("min_width", "最小宽度", 0, 2000), ("min_height", "最小高度", 0, 2000),
                         ("max_width", "最大宽度", 0, 4000), ("max_height", "最大高度", 0, 4000))),
            ("外边距与内容间距", (("margin_x", "水平边距", 0, 100), ("margin_y", "垂直边距", 0, 100),
                              ("spacing", "输入提示与候选间距", 0, 100),
                              ("candidate_spacing", "候选间距", 0, 100),
                              ("label_spacing", "序号间距", 0, 100),
                              ("comment_spacing", "注释间距", 0, 100))),
            ("高亮、边框与阴影", (("hilite_padding_x", "高亮水平内边距", 0, 100),
                               ("hilite_padding_y", "高亮垂直内边距", 0, 100),
                               ("border_width", "边框宽度", 0, 30),
                               ("corner_radius", "窗口圆角", 0, 100),
                               ("hilited_corner_radius", "选中项圆角", 0, 100),
                               ("shadow_radius", "阴影范围", 0, 100),
                               ("shadow_offset_x", "阴影水平偏移", -100, 100),
                               ("shadow_offset_y", "阴影垂直偏移", -100, 100))),
            ("排列辅助与校正", (("linespacing", "行距修正", -100, 100),
                              ("baseline", "基线修正", -100, 100))),
        )
        for group_title, fields in groups:
            group = QGroupBox(group_title, host)
            form = QFormLayout(group)
            for key, label, minimum, maximum in fields:
                combo = _number_combo((0, 4, 8, 12, 16, 24, 32), minimum, maximum, group)
                self._layout_controls[key] = combo
                form.addRow(label + "：", combo)
                combo.currentTextChanged.connect(self._refresh_preview)
            layout.addWidget(group)
        helpers = QGroupBox("排列行为", host)
        helper_form = QFormLayout(helpers)
        self._align = QComboBox(helpers)
        self._align.addItem("顶部", "top"); self._align.addItem("居中", "center"); self._align.addItem("底部", "bottom")
        self._wrap = QCheckBox("内容过长时自动换行", helpers)
        self._reverse = QCheckBox("候选窗位于输入位置上方时自动倒序", helpers)
        helper_form.addRow("文字对齐：", self._align)
        helper_form.addRow(self._wrap)
        helper_form.addRow(self._reverse)
        layout.addWidget(helpers)
        layout.addStretch(1)
        self._candidate_spacing = self._layout_controls["candidate_spacing"]
        self._corner = self._layout_controls["corner_radius"]
        return _scroll_page(host)

    def _build_color_tab(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        note = _help("每项可继承皮肤原值、自定义颜色，或恢复小狼毫默认。颜色统一按常见的 #RRGGBB 显示。", host)
        layout.addWidget(note)
        self._color_rows: dict[str, _ColorRow] = {}
        for group_title, fields in _COLOR_GROUPS:
            group = QGroupBox(group_title, host)
            grid = QGridLayout(group)
            for index, (key, label) in enumerate(fields):
                row = _ColorRow(label, group)
                row.changed.connect(self._refresh_preview)
                self._color_rows[key] = row
                grid.addWidget(row, index // 2, index % 2)
            layout.addWidget(group)
        layout.addStretch(1)
        return _scroll_page(host)

    def _build_weasel_settings(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        notification = QGroupBox("状态提示", host)
        form = QFormLayout(notification)
        self._notifications = QCheckBox("显示中英文切换等状态提示", notification)
        self._notification_time = QSpinBox(notification); self._notification_time.setRange(200, 10000); self._notification_time.setSuffix(" 毫秒")
        self._global_ascii = QCheckBox("所有程序共用同一个中英文状态", notification)
        self._ascii_cursor = QCheckBox("英文状态提示跟随鼠标位置", notification)
        form.addRow(self._notifications)
        form.addRow("提示停留时间：", self._notification_time)
        form.addRow(self._global_ascii)
        form.addRow(self._ascii_cursor)
        layout.addWidget(notification)
        interaction = QGroupBox("候选窗操作", host)
        interaction_form = QFormLayout(interaction)
        self._hover = QComboBox(interaction)
        self._hover.addItem("鼠标移入只高亮", "semi_hilite")
        self._hover.addItem("鼠标移入直接选中", "hilite")
        self._hover.addItem("鼠标移入不处理", "none")
        self._scroll_paging = QCheckBox("滚轮用于翻页", interaction)
        self._click_capture = QCheckBox("点击候选时允许截图辅助定位", interaction)
        self._enhanced_position = QCheckBox("候选窗位置异常时启用增强定位", interaction)
        self._tray_icon = QCheckBox("显示独立的小狼毫托盘图标", interaction)
        self._abbreviate_length = QSpinBox(interaction)
        self._abbreviate_length.setRange(0, 100)
        self._abbreviate_length.setSpecialValueText("不省略")
        self._label_format = QLineEdit(interaction)
        self._label_format.setPlaceholderText("例如 %s.")
        self._mark_text = QLineEdit(interaction)
        self._mark_text.setPlaceholderText("例如 ●")
        self._antialias = QComboBox(interaction)
        self._antialias.addItem("系统默认（推荐）", "default")
        self._antialias.addItem("强制按字体设置", "force_dword")
        self._antialias.addItem("更清晰", "cleartype")
        self._antialias.addItem("灰阶平滑", "grayscale")
        self._antialias.addItem("关闭平滑", "aliased")
        interaction_form.addRow("鼠标悬停：", self._hover)
        interaction_form.addRow(self._scroll_paging)
        interaction_form.addRow(self._click_capture)
        interaction_form.addRow(self._enhanced_position)
        interaction_form.addRow(self._tray_icon)
        interaction_form.addRow("候选词超过多少字时省略：", self._abbreviate_length)
        interaction_form.addRow("候选序号格式：", self._label_format)
        interaction_form.addRow("选中项标记：", self._mark_text)
        interaction_form.addRow("字体平滑：", self._antialias)
        layout.addWidget(interaction)
        applications = QGroupBox("针对特定程序", host)
        app_layout = QVBoxLayout(applications)
        self._app_list = QListWidget(applications)
        self._app_list.setMaximumHeight(120)
        app_layout.addWidget(self._app_list)
        app_form = QFormLayout()
        self._app_exe = QLineEdit(applications)
        self._app_exe.setPlaceholderText("例如 code.exe")
        self._app_ascii = QCheckBox("打开该程序时默认英文", applications)
        self._app_inline = QCheckBox("该程序使用行内输入提示", applications)
        self._app_vim = QCheckBox("兼容 Vim 按键模式", applications)
        app_form.addRow("程序文件名：", self._app_exe)
        app_form.addRow(self._app_ascii)
        app_form.addRow(self._app_inline)
        app_form.addRow(self._app_vim)
        app_layout.addLayout(app_form)
        app_actions = QHBoxLayout()
        self._app_update = QPushButton("添加或更新", applications)
        self._app_remove = QPushButton("删除规则", applications)
        app_actions.addWidget(self._app_update); app_actions.addWidget(self._app_remove); app_actions.addStretch(1)
        app_layout.addLayout(app_actions)
        layout.addWidget(applications)
        layout.addStretch(1)
        self._app_options: dict[str, dict] = {}
        self._app_list.currentItemChanged.connect(self._select_app_rule)
        self._app_update.clicked.connect(self._update_app_rule)
        self._app_remove.clicked.connect(self._remove_app_rule)
        return _scroll_page(host)

    def _build_schema_appearance(self) -> QScrollArea:
        host = QWidget(self)
        layout = QVBoxLayout(host)
        info = _help("这些设置只覆盖当前输入方案，不会改动万象或其他方案的原文件。", host)
        layout.addWidget(info)
        skin = QGroupBox("当前方案皮肤", host)
        form = QFormLayout(skin)
        self._schema_light = QComboBox(skin); self._schema_light.setEditable(True)
        self._schema_dark = QComboBox(skin); self._schema_dark.setEditable(True)
        self._clear_schema_skin = QCheckBox("清除单独覆盖，恢复使用全局皮肤", skin)
        form.addRow("浅色皮肤：", self._schema_light)
        form.addRow("深色皮肤：", self._schema_dark)
        form.addRow(self._clear_schema_skin)
        layout.addWidget(skin)
        icons = QGroupBox("方案图标", host)
        icon_form = QFormLayout(icons)
        self._icon_edits: dict[str, QLineEdit] = {}
        for key, title in (("schema/icon", "方案图标"), ("schema/ascii_icon", "英文图标"),
                           ("schema/full_icon", "全角图标"), ("schema/half_icon", "半角图标")):
            row = QWidget(icons); row_layout = QHBoxLayout(row); row_layout.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(row); browse = QPushButton("选择…", row)
            browse.clicked.connect(lambda _checked=False, target=edit: self._choose_icon(target))
            row_layout.addWidget(edit, 1); row_layout.addWidget(browse)
            icon_form.addRow(title + "：", row)
            self._icon_edits[key] = edit
        layout.addWidget(icons)
        layout.addStretch(1)
        return _scroll_page(host)

    def activate(self) -> None:
        resolution = self._environment_provider()
        name = resolution.files.display_name if resolution.files else "未确认方案"
        self._loaded_scheme_id = str(resolution.schema_id or "")
        self._context.setText(f"当前方案：{name}　｜　写入 weasel.custom.yaml")
        writable = bool(resolution.writable)
        self._save.setEnabled(writable)
        self._save_deploy.setEnabled(writable)
        if not writable:
            self._status.setText("当前方案不可安全确认，本页保持只读：" + resolution.reason)
            return
        try:
            self._service = WeaselSkinService.from_patch_service(self._service_provider())
            self._load_all()
        except Exception as exc:
            self._status.setText(str(exc))
            self._save.setEnabled(False); self._save_deploy.setEnabled(False)

    def _load_all(self) -> None:
        assert self._service is not None
        effective, _custom, self._revision = self._service.read_effective()
        self._populate_catalog()
        style = self._service.load_style().values
        self._load_style(style)
        preferred = str(style.get("color_scheme") or "")
        target = next((self._skin_list.item(i) for i in range(self._skin_list.count())
                       if self._skin_list.item(i).data(Qt.ItemDataRole.UserRole) == preferred), None)
        if target is None and self._skin_list.count():
            target = self._skin_list.item(0)
        if target is not None:
            self._skin_list.setCurrentItem(target)
        self._load_weasel_settings(effective)
        self._load_schema_appearance()
        if self._skin_list.currentItem() is None:
            self._status.setText("尚未发现可用皮肤；仍可调整全局字体和布局。")

    def _populate_catalog(self) -> None:
        assert self._service is not None
        with QSignalBlocker(self._skin_list):
            self._skin_list.clear(); self._color_scheme.clear(); self._color_scheme_dark.clear()
            self._schema_light.clear(); self._schema_dark.clear()
            items = self._service.catalog()
            for item in items:
                row = QListWidgetItem(f"{item.name}\n{item.origin} · {item.scheme_id}")
                row.setData(Qt.ItemDataRole.UserRole, item.scheme_id)
                row.setData(Qt.ItemDataRole.UserRole + 1, item.read_only)
                self._skin_list.addItem(row)
                for combo in (self._color_scheme, self._color_scheme_dark,
                              self._schema_light, self._schema_dark):
                    combo.addItem(item.name, item.scheme_id)

    def _filter_skins(self, text: str) -> None:
        keyword = text.strip().casefold()
        for index in range(self._skin_list.count()):
            item = self._skin_list.item(index)
            item.setHidden(bool(keyword and keyword not in item.text().casefold()))

    def _select_skin(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None or self._service is None:
            return
        scheme_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        try:
            self._draft = self._service.load_scheme(scheme_id)
            self._load_draft(self._draft)
        except Exception as exc:
            self._status.setText(str(exc))

    def _load_draft(self, draft: SkinSchemeDraft) -> None:
        self._loading = True
        self._scheme_id.setText(draft.scheme_id)
        self._scheme_name.setText(draft.name)
        self._author.setText(draft.author)
        self._color_format.setCurrentText(draft.color_format)
        for key, row in self._color_rows.items():
            row.set_rime_value(draft.colors.get(key), draft.color_format)
        self._set_scheme_read_only(draft.read_only)
        self._loading = False
        self._refresh_preview()

    def _set_scheme_read_only(self, read_only: bool) -> None:
        self._scheme_name.setReadOnly(read_only); self._author.setReadOnly(read_only)
        self._color_format.setEnabled(not read_only)
        for row in self._color_rows.values():
            row.mode.setEnabled(not read_only); row.button.setEnabled(not read_only and row.mode.currentData() == "custom")
        self._delete_skin.setEnabled(not read_only)
        self._status.setText(
            "这是只读预设；点击“复制”创建自己的皮肤后再编辑。" if read_only
            else "这是用户自定义皮肤，可以编辑或删除。"
        )

    def _load_style(self, style: dict) -> None:
        layout = style.get("layout") or {}
        mode = str(layout.get("type") or "")
        if not mode:
            if bool(style.get("vertical_text")):
                mode = "vertical_text"
            elif bool(style.get("horizontal", True)):
                mode = "horizontal"
            else:
                mode = "vertical"
            if bool(style.get("fullscreen")):
                mode += "+fullscreen"
        self._layout.setCurrentIndex(max(0, self._layout.findData(mode)))
        self._inline.setChecked(bool(style.get("inline_preedit", True)))
        preedit = str(style.get("preedit_type") or "composition")
        self._preedit.setCurrentIndex(max(0, self._preedit.findData(preedit)))
        self._font.setCurrentFont(QFont(str(style.get("font_face") or "Microsoft YaHei UI")))
        self._label_font.setCurrentFont(QFont(str(style.get("label_font_face") or style.get("font_face") or "Microsoft YaHei UI")))
        self._comment_font.setCurrentFont(QFont(str(style.get("comment_font_face") or style.get("font_face") or "Microsoft YaHei UI")))
        self._font_point.setCurrentText(str(style.get("font_point") or 14))
        self._label_font_point.setCurrentText(str(style.get("label_font_point") or 12))
        self._comment_font_point.setCurrentText(str(style.get("comment_font_point") or 12))
        self._font_fallback.setText(str(style.get("font_face_fallback") or ""))
        self._font_range.setText(str(style.get("font_range") or ""))
        weight = str(style.get("font_weight") or "normal")
        self._font_weight.setCurrentIndex(2 if weight == "bold" else 1 if weight == "medium" else 0)
        self._font_style.setCurrentIndex(1 if str(style.get("font_style") or "normal") == "italic" else 0)
        self._set_combo_data(self._color_scheme, str(style.get("color_scheme") or "aqua"))
        self._set_combo_data(self._color_scheme_dark, str(style.get("color_scheme_dark") or ""))
        self._follow_system.setChecked(bool(style.get("color_scheme_dark")))
        for key, control in self._layout_controls.items():
            control.setCurrentText(str(layout.get(key, 8 if key in ("candidate_spacing", "corner_radius") else 0)))
        self._align.setCurrentIndex(max(0, self._align.findData(layout.get("align_type", "center"))))
        self._wrap.setChecked(bool(layout.get("vertical_text_with_wrap", False)))
        self._reverse.setChecked(bool(layout.get("vertical_auto_reverse", False)))
        self._refresh_preview()

    def _load_weasel_settings(self, effective: dict) -> None:
        style = effective.get("style") or {}
        self._notifications.setChecked(bool(effective.get("show_notifications", True)))
        self._notification_time.setValue(int(effective.get("show_notifications_time", 1200) or 1200))
        self._global_ascii.setChecked(bool(effective.get("global_ascii", False)))
        self._ascii_cursor.setChecked(bool(style.get("ascii_tip_follow_cursor", False)))
        self._set_combo_data(self._hover, str(style.get("hover_type") or "none"))
        self._scroll_paging.setChecked(bool(style.get("paging_on_scroll", True)))
        self._click_capture.setChecked(bool(style.get("click_to_capture", False)))
        self._enhanced_position.setChecked(bool(style.get("enhanced_position", False)))
        self._tray_icon.setChecked(bool(style.get("display_tray_icon", False)))
        self._abbreviate_length.setValue(int(style.get("candidate_abbreviate_length", 0) or 0))
        self._label_format.setText(str(style.get("label_format") or "%s."))
        self._mark_text.setText(str(style.get("mark_text") or ""))
        self._set_combo_data(self._antialias, str(style.get("antialias_mode") or "default"))
        raw_apps = effective.get("app_options") or {}
        self._app_options = {
            str(name): dict(options) for name, options in raw_apps.items()
            if isinstance(options, dict)
        } if isinstance(raw_apps, dict) else {}
        self._refresh_app_list()

    def _load_schema_appearance(self) -> None:
        if self._service is None or not self._loaded_scheme_id:
            return
        draft, self._schema_revision = self._service.read_schema_appearance(self._loaded_scheme_id)
        self._set_combo_data(self._schema_light, str(draft.values.get("style/color_scheme") or ""))
        self._set_combo_data(self._schema_dark, str(draft.values.get("style/color_scheme_dark") or ""))
        for key, edit in self._icon_edits.items():
            edit.setText(str(draft.values.get(key) or ""))
        self._clear_schema_skin.setChecked(False)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(value)

    def _on_preview_text_edited(self, text: str) -> None:
        self._preview_raw = text or "wanxiang"
        self._refresh_preview()

    def _refresh_preview(self, *_args) -> None:
        if not hasattr(self, "_preview_row") or self._loading:
            return
        mode = str(self._layout.currentData() or "horizontal")
        fullscreen = mode.endswith("+fullscreen")
        vertical_text = mode == "vertical_text"
        vertical = mode in ("vertical", "vertical+fullscreen")
        self._preview_row.setDirection(
            QBoxLayout.Direction.RightToLeft if vertical_text else
            QBoxLayout.Direction.TopToBottom if vertical else
            QBoxLayout.Direction.LeftToRight
        )
        spacing = _number_value(self._candidate_spacing, 8)
        self._preview_row.setSpacing(max(2, spacing))
        words = ("你好", "输入法", "万象拼音", "配置工具")
        font = QFont(self._font.currentFont().family() or "Microsoft YaHei UI",
                     _number_value(self._font_point, 14))
        for index, (label, word) in enumerate(zip(self._preview_labels, words), 1):
            label.setText("\n".join((str(index), *word)) if vertical_text else f"{index} {word}")
            label.setFont(font)
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter if vertical_text else Qt.AlignmentFlag.AlignVCenter)
        descriptions = {
            "horizontal": "普通候选窗 · 候选横向排列",
            "vertical": "普通候选窗 · 候选逐行排列，文字横着读",
            "vertical_text": "普通候选窗 · 文字从上到下书写",
            "horizontal+fullscreen": "全屏候选窗 · 候选横向排列",
            "vertical+fullscreen": "全屏候选窗 · 候选逐行排列",
        }
        help_text = {
            "horizontal": "候选词在一行内从左到右排列。",
            "vertical": "候选词从上到下逐行排列，但每个词仍横着阅读。",
            "vertical_text": "候选词中的汉字也从上到下书写，类似传统竖排。",
            "horizontal+fullscreen": "候选窗占满屏幕，候选词在其中横向排列。",
            "vertical+fullscreen": "候选窗占满屏幕，候选词在其中逐行排列。",
        }
        self._preview_mode.setText(descriptions.get(mode, descriptions["horizontal"]))
        self._layout_help.setText(help_text.get(mode, help_text["horizontal"]))
        self._preview_fullscreen.setVisible(fullscreen)
        self._preview_surface.setMaximumWidth(16777215 if fullscreen else 620)
        preedit = str(self._preedit.currentData() or "composition")
        values = {"composition": self._preview_raw, "preview": "你好", "preview_all": "你好，欢迎使用万象拼音"}
        explanations = {
            "composition": "显示当前键入的拼音字母，例如 wanxiang。",
            "preview": "显示当前高亮的候选字词，例如“你好”。",
            "preview_all": "显示整段输入转换后的结果，适合输入长句时预先确认。",
        }
        value = values.get(preedit, self._preview_raw)
        self._preedit_help.setText(explanations.get(preedit, ""))
        inline = self._inline.isChecked()
        with QSignalBlocker(self._preview_input):
            self._preview_input.setReadOnly(not inline or preedit != "composition")
            self._preview_input.setText(value if inline else "")
        self._preview_preedit.setText(f"输入提示：{value}")
        self._preview_preedit.setVisible(not inline)
        background, foreground, selected = "#f8fbff", "#162436", "#dbeafe"
        if self._preview_theme.currentData() == "dark":
            background, foreground, selected = "#242424", "#f4f4f4", "#2f6658"
        for key, fallback_name in (("back_color", "background"), ("candidate_text_color", "foreground"),
                                   ("hilited_candidate_back_color", "selected")):
            row = self._color_rows.get(key)
            if row and row.mode.currentData() == "custom":
                if fallback_name == "background": background = row._hex[:7]
                elif fallback_name == "foreground": foreground = row._hex[:7]
                else: selected = row._hex[:7]
        radius = _number_value(self._corner, 8)
        self._preview_surface.setStyleSheet(
            f"QFrame#CandidateAppearancePreview {{background:{background}; color:{foreground}; border:1px solid {foreground}; border-radius:{radius}px;}}"
            f"QFrame#CandidateAppearancePreview QLabel {{color:{foreground}; padding:4px 7px;}}"
            f"QFrame#CandidateAppearancePreview QLabel[selected='true'] {{background:{selected}; border-radius:{max(2, radius // 2)}px;}}"
        )

    def _style_updates(self) -> dict:
        mode = str(self._layout.currentData() or "horizontal")
        updates = {
            "style/layout/type": mode,
            "style/inline_preedit": self._inline.isChecked(),
            "style/preedit_type": self._preedit.currentData(),
            "style/font_face": self._font.currentFont().family(),
            "style/font_point": _number_value(self._font_point, 14),
            "style/label_font_face": self._label_font.currentFont().family(),
            "style/label_font_point": _number_value(self._label_font_point, 12),
            "style/comment_font_face": self._comment_font.currentFont().family(),
            "style/comment_font_point": _number_value(self._comment_font_point, 12),
            "style/color_scheme": self._color_scheme.currentData() or self._color_scheme.currentText().strip(),
            "style/font_weight": ("bold" if self._font_weight.currentIndex() == 2 else "medium" if self._font_weight.currentIndex() == 1 else "normal"),
            "style/font_style": "italic" if self._font_style.currentIndex() == 1 else "normal",
        }
        if self._follow_system.isChecked():
            updates["style/color_scheme_dark"] = self._color_scheme_dark.currentData() or self._color_scheme_dark.currentText().strip()
        if self._font_fallback.text().strip(): updates["style/font_face_fallback"] = self._font_fallback.text().strip()
        if self._font_range.text().strip(): updates["style/font_range"] = self._font_range.text().strip()
        updates.update({f"style/layout/{key}": _number_value(control, 0) for key, control in self._layout_controls.items()})
        updates["style/layout/align_type"] = self._align.currentData()
        updates["style/layout/vertical_auto_reverse"] = self._reverse.isChecked()
        updates["style/layout/vertical_text_with_wrap"] = self._wrap.isChecked()
        return updates

    def _scheme_updates(self) -> dict:
        if self._draft is None or self._draft.read_only:
            return {}
        sid = self._draft.scheme_id
        fmt = self._color_format.currentText()
        updates = {
            f"preset_color_schemes/{sid}/name": self._scheme_name.text().strip() or sid,
            f"preset_color_schemes/{sid}/author": self._author.text().strip(),
            f"preset_color_schemes/{sid}/color_format": fmt,
        }
        for key, row in self._color_rows.items():
            value = row.rime_value(fmt)
            if value is not None:
                updates[f"preset_color_schemes/{sid}/{key}"] = value
        return updates

    def _scheme_remove_keys(self) -> tuple[str, ...]:
        if self._draft is None or self._draft.read_only:
            return ()
        prefix = f"preset_color_schemes/{self._draft.scheme_id}"
        return tuple(
            f"{prefix}/{key}" for key, row in self._color_rows.items()
            if row.mode.currentData() != "custom"
        )

    def _weasel_setting_updates(self) -> dict:
        return {
            "show_notifications": self._notifications.isChecked(),
            "show_notifications_time": self._notification_time.value(),
            "global_ascii": self._global_ascii.isChecked(),
            "style/ascii_tip_follow_cursor": self._ascii_cursor.isChecked(),
            "style/hover_type": self._hover.currentData(),
            "style/paging_on_scroll": self._scroll_paging.isChecked(),
            "style/click_to_capture": self._click_capture.isChecked(),
            "style/enhanced_position": self._enhanced_position.isChecked(),
            "style/display_tray_icon": self._tray_icon.isChecked(),
            "style/candidate_abbreviate_length": self._abbreviate_length.value(),
            "style/label_format": self._label_format.text().strip() or "%s.",
            "style/mark_text": self._mark_text.text(),
            "style/antialias_mode": self._antialias.currentData(),
            "app_options": self._app_options,
        }

    def _refresh_app_list(self) -> None:
        self._app_list.clear()
        for name in sorted(self._app_options, key=str.casefold):
            options = self._app_options[name]
            labels = []
            if options.get("ascii_mode"): labels.append("默认英文")
            if options.get("inline_preedit"): labels.append("行内提示")
            if options.get("vim_mode"): labels.append("Vim")
            item = QListWidgetItem(f"{name}　{'、'.join(labels) or '使用全局设置'}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._app_list.addItem(item)

    def _select_app_rule(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        options = self._app_options.get(name, {})
        self._app_exe.setText(name)
        self._app_ascii.setChecked(bool(options.get("ascii_mode")))
        self._app_inline.setChecked(bool(options.get("inline_preedit")))
        self._app_vim.setChecked(bool(options.get("vim_mode")))

    def _update_app_rule(self) -> None:
        name = self._app_exe.text().strip().lower()
        if not name or "/" in name or "\\" in name:
            QMessageBox.warning(self, "程序规则", "请输入程序文件名，例如 code.exe。")
            return
        self._app_options[name] = {
            "ascii_mode": self._app_ascii.isChecked(),
            "inline_preedit": self._app_inline.isChecked(),
            "vim_mode": self._app_vim.isChecked(),
        }
        self._refresh_app_list()
        self._status.setText("程序规则已加入页面；点击保存后才会写入。")

    def _remove_app_rule(self) -> None:
        name = self._app_exe.text().strip().lower()
        if name in self._app_options:
            self._app_options.pop(name)
            self._refresh_app_list()
            self._app_exe.clear()
            self._status.setText("程序规则已从页面移除；点击保存后才会生效。")

    def _save_changes(self, deploy: bool) -> None:
        if self._service is None or self._revision is None:
            return
        resolution = self._environment_provider()
        if str(resolution.schema_id or "") != self._loaded_scheme_id:
            QMessageBox.warning(self, "方案已变化", "输入方案已切换，请重新进入本页后再保存。")
            self.activate(); return
        try:
            if self._sections.currentIndex() == 2:
                self._save_schema()
                saved_path = self._service.root / f"{self._loaded_scheme_id}.custom.yaml"
            else:
                updates = self._style_updates()
                updates.update(self._scheme_updates())
                updates.update(self._weasel_setting_updates())
                remove_keys = list(self._scheme_remove_keys())
                if not self._follow_system.isChecked():
                    remove_keys.append("style/color_scheme_dark")
                saved_path = self._service.save_updates(
                    "weasel.custom.yaml", updates, self._revision,
                    remove_keys=tuple(remove_keys),
                )
            self._status.setText(f"已保存：{saved_path.name}" + ("；正在重新部署。" if deploy else "；尚未重新部署。"))
            self.saved.emit(str(saved_path))
            self._load_all()
            if deploy and self._deploy_request is not None:
                self._deploy_request()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def _save_schema(self) -> None:
        assert self._service is not None and self._schema_revision is not None
        values = {key: edit.text().strip() for key, edit in self._icon_edits.items() if edit.text().strip()}
        if not self._clear_schema_skin.isChecked():
            values["style/color_scheme"] = self._schema_light.currentData() or self._schema_light.currentText().strip()
            values["style/color_scheme_dark"] = self._schema_dark.currentData() or self._schema_dark.currentText().strip()
        filename = f"{self._loaded_scheme_id}.custom.yaml"
        self._service.save_updates(
            filename, values, self._schema_revision,
            remove_keys=("style/color_scheme", "style/color_scheme_dark")
            if self._clear_schema_skin.isChecked() else (),
        )

    def _reload_current(self) -> None:
        if self._service is not None:
            self._load_all()

    def _ask_skin_id(self, title: str, suggestion: str) -> str:
        value, ok = QInputDialog.getText(self, title, "皮肤 ID（英文、数字、下划线或连字符）：", text=suggestion)
        if not ok: return ""
        sid = re.sub(r"[^a-zA-Z0-9_-]", "_", value.strip()).strip("_")
        if not sid:
            QMessageBox.warning(self, title, "请输入有效的皮肤 ID。")
        return sid

    def _create_skin(self) -> None:
        sid = self._ask_skin_id("新建皮肤", "my_skin")
        if not sid or self._service is None or self._revision is None: return
        draft = SkinSchemeDraft(sid, "我的皮肤", "", "ABGR", {}, "用户自定义", False)
        try:
            self._service.save_scheme(draft, self._revision); self._load_all(); self._select_list_id(sid)
        except Exception as exc: QMessageBox.warning(self, "新建失败", str(exc))

    def _copy_current_skin(self) -> None:
        if self._draft is None or self._service is None or self._revision is None: return
        sid = self._ask_skin_id("复制皮肤", f"{self._draft.scheme_id}_custom")
        if not sid: return
        draft = SkinSchemeDraft(sid, self._draft.name + "（副本）", self._draft.author,
                                self._draft.color_format, dict(self._draft.colors), "用户自定义", False)
        try:
            self._service.save_scheme(draft, self._revision); self._load_all(); self._select_list_id(sid)
        except Exception as exc: QMessageBox.warning(self, "复制失败", str(exc))

    def _delete_current_skin(self) -> None:
        if self._draft is None or self._draft.read_only or self._service is None or self._revision is None: return
        if QMessageBox.question(self, "删除皮肤", f"确定删除自定义皮肤“{self._draft.name}”吗？\n删除前会自动备份配置文件。") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.delete_custom_scheme(self._draft.scheme_id, self._revision); self._load_all()
        except Exception as exc: QMessageBox.warning(self, "删除失败", str(exc))

    def _select_list_id(self, sid: str) -> None:
        for index in range(self._skin_list.count()):
            item = self._skin_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == sid:
                self._skin_list.setCurrentItem(item); return

    def _import_scheme(self) -> None:
        if self._service is None or self._revision is None: return
        filename, _ = QFileDialog.getOpenFileName(self, "导入皮肤", "", "YAML 文件 (*.yaml *.yml)")
        if not filename: return
        try:
            draft = self._service.import_scheme(Path(filename).read_text(encoding="utf-8-sig"))
            self._service.save_scheme(draft, self._revision); self._load_all(); self._select_list_id(draft.scheme_id)
        except Exception as exc: QMessageBox.warning(self, "导入失败", str(exc))

    def _current_draft_from_ui(self) -> SkinSchemeDraft | None:
        if self._draft is None: return None
        fmt = self._color_format.currentText()
        return SkinSchemeDraft(self._draft.scheme_id, self._scheme_name.text().strip(), self._author.text().strip(),
                               fmt, {key: row.rime_value(fmt) for key, row in self._color_rows.items()},
                               self._draft.origin, self._draft.read_only)

    def _export_scheme(self) -> None:
        draft = self._current_draft_from_ui()
        if draft is None or self._service is None: return
        filename, selected = QFileDialog.getSaveFileName(self, "导出皮肤", f"{draft.scheme_id}.yaml",
                                                         "YAML 文件 (*.yaml);;皮肤包 (*.zip)")
        if not filename: return
        try:
            path = Path(filename)
            if path.suffix.lower() == ".zip" or "皮肤包" in selected:
                preview = self._service.root / "preview" / f"color_scheme_{draft.scheme_id}.png"
                preview.parent.mkdir(parents=True, exist_ok=True)
                self._preview_surface.grab().save(str(preview), "PNG")
                self._service.export_package(draft, path, (preview,))
            else:
                path.write_text(self._service.scheme_yaml(draft), encoding="utf-8")
            self._status.setText(f"已导出：{path.name}")
        except Exception as exc: QMessageBox.warning(self, "导出失败", str(exc))

    def _copy_scheme_yaml(self) -> None:
        draft = self._current_draft_from_ui()
        if draft is None or self._service is None: return
        QApplication.clipboard().setText(self._service.scheme_yaml(draft))
        self._status.setText("当前皮肤 YAML 已复制到剪贴板。")

    def _choose_icon(self, target: QLineEdit) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择图标", "", "图标文件 (*.ico *.png)")
        if filename: target.setText(filename)

    def _apply_preset(self, name: str) -> None:
        values = {
            "compact": ("horizontal", {"candidate_spacing": 4, "margin_x": 8, "margin_y": 6, "corner_radius": 6}),
            "vertical": ("vertical", {"candidate_spacing": 8, "margin_x": 12, "margin_y": 10, "corner_radius": 8}),
            "rounded": ("horizontal", {"candidate_spacing": 10, "corner_radius": 18, "hilited_corner_radius": 14}),
            "win11": ("horizontal", {"candidate_spacing": 8, "corner_radius": 8, "shadow_radius": 12, "margin_x": 12}),
        }
        mode, fields = values[name]
        self._layout.setCurrentIndex(self._layout.findData(mode))
        for key, value in fields.items(): self._layout_controls[key].setCurrentText(str(value))
        self._status.setText("预设已填入页面；点击保存后才会写入。")
