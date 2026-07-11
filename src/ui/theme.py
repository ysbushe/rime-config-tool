"""主题加载与应用（单模板 + 占位符注入机制）。

- 三套皮肤共用一份 ``application.qss`` 模板，色值/字体写成 ``@TOKEN@`` 占位符；
- 切换时按当前主题令牌字典做字符串替换后再 ``setStyleSheet``，改一处全局生效；
- 将相对 ``url(check.svg)`` 替换为绝对 file URI，确保勾选白勾在源码运行与
  PyInstaller 打包后均能加载；
- ``apply_theme`` 作用于整个 ``QApplication``，使之后打开的弹窗自动继承当前主题；
- 水墨(ink)皮肤额外显隐 MainWindow 注册的常驻装饰控件（渐变条 + 朱砂印章）。

主题名：``light`` / ``dark`` / ``ink``（未知主题回退 light）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

_UI_DIR = Path(__file__).resolve().parent

# 单模板：三套皮肤共用一份（占位符在切换时注入）
_TEMPLATE = "application.qss"

# --------------------------------------------------------------------------- #
# 三套皮肤令牌（直接抄 design/themes/TOKENS.md §1.1 权威值）
# --------------------------------------------------------------------------- #
THEME_TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "@BG_APP@": "#F3F3F3",
        "@BG_SURFACE@": "#FFFFFF",
        "@BG_SIDEBAR@": "#FAFAFA",
        "@BG_ELEVATED@": "#FFFFFF",
        "@BG_HOVER@": "#EDEDED",
        "@BG_ACTIVE_NAV@": "#E6F0F9",
        "@BORDER@": "#E5E5E5",
        "@BORDER_STRONG@": "#D8D8D8",
        "@TEXT_PRIMARY@": "#1B1B1B",
        "@TEXT_SECONDARY@": "#5A6066",
        "@TEXT_MUTED@": "#8A8A8A",
        "@ACCENT@": "#185FA5",
        "@ACCENT_HOVER@": "#14507F",
        "@ACCENT_SOFT@": "#E6F0F9",
        "@ON_ACCENT@": "#FFFFFF",
        "@SELECTION_BG@": "#185FA5",
        "@SELECTION_TEXT@": "#FFFFFF",
        "@TITLEBAR_BG@": "#F3F3F3",
        "@TITLEBAR_TEXT@": "#1B1B1B",
        "@SUCCESS_TEXT@": "#1E7E45",
        "@SUCCESS_BG@": "#EAF4EE",
        "@CONFLICT_BG@": "#FFF7E6",
        "@CONFLICT_BORDER@": "#F2D69A",
        "@CONFLICT_TEXT@": "#B26A00",
        "@SEAL@": "",
        "@FONT_UI@": '"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif',
        "@FONT_HEADING@": '"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif',
        "@FONT_MONO@": '"Cascadia Code","Consolas",monospace',
    },
    "dark": {
        "@BG_APP@": "#1A1A1A",
        "@BG_SURFACE@": "#202020",
        "@BG_SIDEBAR@": "#1E1E1E",
        "@BG_ELEVATED@": "#232323",
        "@BG_HOVER@": "#272727",
        "@BG_ACTIVE_NAV@": "#243245",
        "@BORDER@": "#2E2E2E",
        "@BORDER_STRONG@": "#33373B",
        "@TEXT_PRIMARY@": "#ECECEC",
        "@TEXT_SECONDARY@": "#9AA0A6",
        "@TEXT_MUTED@": "#74797F",
        "@ACCENT@": "#4A9EFF",
        "@ACCENT_HOVER@": "#62ACFF",
        "@ACCENT_SOFT@": "#243245",
        "@ON_ACCENT@": "#0E0E0E",
        "@SELECTION_BG@": "#2E6FAE",
        "@SELECTION_TEXT@": "#FFFFFF",
        "@TITLEBAR_BG@": "#202020",
        "@TITLEBAR_TEXT@": "#ECECEC",
        "@SUCCESS_TEXT@": "#5FD08A",
        "@SUCCESS_BG@": "#1C2C22",
        "@CONFLICT_BG@": "#2E2519",
        "@CONFLICT_BORDER@": "#6B4F26",
        "@CONFLICT_TEXT@": "#E0A030",
        "@SEAL@": "",
        "@FONT_UI@": '"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif',
        "@FONT_HEADING@": '"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif',
        "@FONT_MONO@": '"Cascadia Code","Consolas",monospace',
    },
    "ink": {
        "@BG_APP@": "#F5F1E8",
        "@BG_SURFACE@": "#FBF8F0",
        "@BG_SIDEBAR@": "#EBE2CF",
        "@BG_ELEVATED@": "#FBF8F0",
        "@BG_HOVER@": "#F1E8D6",
        "@BG_ACTIVE_NAV@": "#2F5D50",
        "@BORDER@": "#D8CDB8",
        "@BORDER_STRONG@": "#CDBE9F",
        "@TEXT_PRIMARY@": "#2B2620",
        "@TEXT_SECONDARY@": "#7A6E52",
        "@TEXT_MUTED@": "#A1916F",
        "@ACCENT@": "#2F5D50",
        "@ACCENT_HOVER@": "#264C41",
        "@ACCENT_SOFT@": "#E3EFE8",
        "@ON_ACCENT@": "#F3EFE4",
        "@SELECTION_BG@": "#2F5D50",
        "@SELECTION_TEXT@": "#F3EFE4",
        "@TITLEBAR_BG@": "#FBF8F0",
        "@TITLEBAR_TEXT@": "#2B2620",
        "@SUCCESS_TEXT@": "#2F5D50",
        "@SUCCESS_BG@": "#E3EFE8",
        "@CONFLICT_BG@": "#F8E7E2",
        "@CONFLICT_BORDER@": "#E6B4A6",
        "@CONFLICT_TEXT@": "#B23A2E",
        "@SEAL@": "#B23A2E",
        "@FONT_UI@": '"Microsoft YaHei","PingFang SC",sans-serif',
        "@FONT_HEADING@": '"Source Han Serif SC","Noto Serif SC","SimSun",serif',
        "@FONT_MONO@": '"Cascadia Code","Consolas",monospace',
    },
}

_current_theme = "light"
_current_tokens = THEME_TOKENS["light"]

# 水墨装饰控件引用（由 MainWindow 通过 set_ink_decor 注册）
_INK_DECOR = None
_INK_SEAL = None


def qss_path(theme: str) -> Path:
    """返回 QSS 模板路径（单模板机制：三套皮肤共用一份，颜色经 token 注入）。"""
    return _UI_DIR / _TEMPLATE


def load_theme_qss(theme: str) -> str:
    """读取模板 QSS，按当前主题令牌字典替换占位符，并把 check.svg 替换为绝对 URI。"""
    p = qss_path(theme)
    qss = p.read_text(encoding="utf-8")
    tokens = THEME_TOKENS.get(theme, THEME_TOKENS["light"])
    for key, value in tokens.items():
        qss = qss.replace(key, value)
    check_svg = _UI_DIR / "check.svg"
    if check_svg.exists():
        qss = qss.replace("url(check.svg)", "url(%s)" % check_svg.as_uri())
    return qss


def apply_theme(theme: str) -> None:
    """应用主题到整个 QApplication（所有窗口与弹窗继承）。

    - 未知主题回退 light；
    - 末尾按是否为 ink 显隐已注册的水墨装饰控件（未注册则跳过）。
    """
    global _current_theme, _current_tokens
    if theme not in THEME_TOKENS:
        theme = "light"
    _current_theme = theme
    _current_tokens = THEME_TOKENS[theme]

    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(load_theme_qss(_current_theme))

    # 水墨装饰：仅 ink 主题显示，切走隐藏（零抖动）
    is_ink = (theme == "ink")
    for widget in (_INK_DECOR, _INK_SEAL):
        if widget is None:
            continue
        try:
            widget.setHidden(not is_ink)
        except RuntimeError:
            # 控件可能已随窗口销毁，残留引用忽略
            pass


def apply_window_theme(window: QWidget, theme: str) -> None:
    """Apply native Windows title-bar colors for the active theme when supported."""
    if not hasattr(ctypes, "windll"):
        return
    if theme not in THEME_TOKENS:
        theme = "light"
    tokens = THEME_TOKENS[theme]
    try:
        hwnd = wintypes.HWND(int(window.winId()))
        dark = wintypes.BOOL(theme == "dark")
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)
            )
        _set_dwm_color(hwnd, 35, tokens["@TITLEBAR_BG@"])
        _set_dwm_color(hwnd, 36, tokens["@TITLEBAR_TEXT@"])
        _set_dwm_color(hwnd, 34, tokens["@BORDER_STRONG@"])
    except Exception:
        return


def _set_dwm_color(hwnd, attr: int, color: str) -> None:
    value = wintypes.DWORD(_colorref(color))
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
    )


def _colorref(hex_color: str) -> int:
    color = hex_color.lstrip("#")
    red = int(color[0:2], 16)
    green = int(color[2:4], 16)
    blue = int(color[4:6], 16)
    return red | (green << 8) | (blue << 16)


def set_ink_decor(decor, seal) -> None:
    """注册 MainWindow 的水墨装饰控件（渐变条 + 印章），供 apply_theme 显隐。"""
    global _INK_DECOR, _INK_SEAL
    _INK_DECOR = decor
    _INK_SEAL = seal


def current_theme() -> str:
    """当前生效主题（light / dark / ink）。"""
    return _current_theme


def accent_color() -> str:
    """当前主题主色（@ACCENT@ 令牌值），供业务组件（如分组『全部』按钮）取用。"""
    return _current_tokens["@ACCENT@"]


def conflict_background() -> QColor:
    """冲突行高亮背景，随当前主题变化。"""
    return QColor(_current_tokens["@CONFLICT_BG@"])


def selection_background() -> QColor:
    """Selected-row background, kept high contrast for each theme."""
    return QColor(_current_tokens["@SELECTION_BG@"])


def selection_text() -> QColor:
    """Selected-row text color, kept high contrast for each theme."""
    return QColor(_current_tokens["@SELECTION_TEXT@"])
