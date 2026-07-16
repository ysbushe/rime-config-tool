"""三套皮肤（light/dark/ink）主题机制测试（基于临时副本，不碰真实 Rime 配置）。

覆盖：
    - apply_theme 三主题均能把模板占位符替换完（无 @XXX@ 残留）
    - 模板所用占位符均在 THEME_TOKENS 有定义
    - THEME_TOKENS 三套各含 34 令牌，A/B 的 @SEAL@ 为 ""
    - conflict_background() / accent_color() 返回当前主题值
    - ink 装饰在 ink 时显示、非 ink 隐藏
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QImage, QPalette, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QLabel

from src.ui import theme
from src.ui._theme_template import QSS_TEMPLATE
from src.ui.theme import (
    THEME_TOKENS,
    accent_color,
    apply_theme,
    conflict_background,
    current_theme,
    load_theme_qss,
)

_ALL_TOKENS = [
    "@BG_APP@", "@BG_SURFACE@", "@BG_SIDEBAR@", "@BG_ELEVATED@", "@BG_HOVER@",
    "@BG_ACTIVE_NAV@", "@BORDER@", "@BORDER_STRONG@", "@TABLE_GRID@", "@TEXT_PRIMARY@",
    "@TEXT_SECONDARY@", "@TEXT_MUTED@", "@ACCENT@", "@ACCENT_HOVER@",
    "@ACCENT_SOFT@", "@ON_ACCENT@", "@SUCCESS_TEXT@", "@SUCCESS_BG@",
    "@CONFLICT_BG@", "@CONFLICT_BORDER@", "@CONFLICT_TEXT@", "@SEAL@",
    "@SELECTION_BG@", "@SELECTION_TEXT@", "@TITLEBAR_BG@", "@TITLEBAR_TEXT@",
    "@FONT_UI@", "@FONT_HEADING@", "@FONT_MONO@",
    "@INFO_TEXT@", "@WARNING_TEXT@", "@ERROR_TEXT@", "@NEUTRAL_TEXT@",
    "@DUPLICATE_TEXT@",
]


def test_theme_tokens_count_and_seal() -> None:
    assert set(THEME_TOKENS.keys()) == {"light", "dark", "ink"}
    for name, tokens in THEME_TOKENS.items():
        assert len(tokens) == 34, f"{name} 应有 34 个令牌，实际 {len(tokens)}"
        for tk in _ALL_TOKENS:
            assert tk in tokens, f"{name} 缺少令牌 {tk}"
        # A/B 的印章令牌为空；C 为朱砂
        if name in ("light", "dark"):
            assert tokens["@SEAL@"] == "", f"{name} 的 @SEAL@ 应为空"
        else:
            assert tokens["@SEAL@"] == "#B23A2E"


def test_template_only_uses_defined_tokens() -> None:
    tpl = (Path(__file__).resolve().parent.parent / "src/ui/application.qss").read_text(
        encoding="utf-8")
    used = {f"@{m}@" for m in re.findall(r"@([A-Z_]+)@", tpl)}
    defined = set(_ALL_TOKENS)
    assert used <= defined, f"模板使用了未定义令牌: {used - defined}"


def test_qss_path_uses_pyinstaller_bundle_ui_resources(tmp_path, monkeypatch) -> None:
    bundled_ui = tmp_path / "src" / "ui"
    bundled_ui.mkdir(parents=True)
    monkeypatch.setattr(theme.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert theme.qss_path("light") == bundled_ui / "application.qss"


def test_frozen_build_uses_embedded_theme_template(tmp_path, monkeypatch) -> None:
    """发布版不依赖可移动目录中的 QSS 文件读取。"""
    monkeypatch.setattr(theme.sys, "frozen", True, raising=False)
    monkeypatch.setattr(theme.sys, "_MEIPASS", str(tmp_path), raising=False)

    css = theme.load_theme_qss("light")
    assert "@BG_APP@" not in css
    assert "QDialog" in css
    assert QSS_TEMPLATE.startswith("/* RimeConfig")


def test_apply_theme_replaces_all_placeholders(qapp) -> None:
    for name in ("light", "dark", "ink"):
        apply_theme(name)
        css = qapp.styleSheet()
        # 不应残留任何 @XXX@ 占位符
        for tk in _ALL_TOKENS:
            assert tk not in css, f"{name} 主题仍有未替换占位符 {tk}"
        # 勾选图标必须使用不受发布目录影响的 Qt 内置资源 URI。
        assert "url(check.svg)" not in css
        assert f":/rimeconfig/check-{name}.svg" in css
        assert ":/rimeconfig/check-disabled.svg" in css
        assert "file://" not in css


def test_frozen_theme_parses_after_relocation_to_unicode_path(tmp_path, monkeypatch, qapp) -> None:
    """发布目录含中文、空格时，主题仍必须完整解析并绘制弹窗背景。"""
    bundle_root = tmp_path / "中文 备份" / "RimeConfig" / "_internal"
    (bundle_root / "src" / "ui").mkdir(parents=True)
    monkeypatch.setattr(theme.sys, "frozen", True, raising=False)
    monkeypatch.setattr(theme.sys, "_MEIPASS", str(bundle_root), raising=False)

    css = theme.load_theme_qss("light")
    assert "file://" not in css
    assert ":/rimeconfig/check-light.svg" in css
    assert not QPixmap(":/rimeconfig/check-light.svg").isNull()

    qapp.setStyleSheet(css)
    dialog = QDialog()
    dialog.resize(120, 80)
    dialog.ensurePolished()
    image = QImage(dialog.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    dialog.render(image)
    assert image.pixelColor(4, 4).name().upper() == "#F3F3F3"


def test_apply_theme_sets_dialog_fallback_palette(qapp) -> None:
    apply_theme("light")
    light_dialog = QDialog()
    qapp.sendEvent(light_dialog, theme.QEvent(theme.QEvent.Type.Polish))
    assert light_dialog.testAttribute(theme.Qt.WidgetAttribute.WA_StyledBackground)
    assert light_dialog.palette().color(QPalette.ColorRole.Window).name().upper() == "#F3F3F3"
    assert light_dialog.palette().color(QPalette.ColorRole.WindowText).name().upper() == "#1B1B1B"

    apply_theme("dark")
    dark_dialog = QDialog()
    assert dark_dialog.palette().color(QPalette.ColorRole.Window).name().upper() == "#1A1A1A"


def test_accent_and_conflict_follow_theme(qapp) -> None:
    apply_theme("light")
    assert accent_color() == "#185FA5"
    assert conflict_background().name().upper() == "#FFF7E6"
    apply_theme("dark")
    assert accent_color() == "#4A9EFF"
    assert conflict_background().name().upper() == "#2E2519"
    apply_theme("ink")
    assert accent_color() == "#2F5D50"
    assert conflict_background().name().upper() == "#F8E7E2"


def test_ink_decor_visibility_follows_theme(qapp) -> None:
    # 用 QLabel 作宿主（任意 QWidget 均可），装饰件绝对定位、默认隐藏
    host = QLabel("host")
    decor = QFrame(host)
    decor.setObjectName("InkDecor")
    seal = QLabel("藏", host)
    seal.setObjectName("InkSeal")
    decor.setHidden(True)
    seal.setHidden(True)
    theme.set_ink_decor(decor, seal)
    try:
        apply_theme("ink")
        assert decor.isHidden() is False
        # 朱砂印章会遮挡主窗口左上内容，主题 C 仅保留贴边细线。
        assert seal.isHidden() is True

        apply_theme("dark")
        assert decor.isHidden() is True
        assert seal.isHidden() is True

        apply_theme("light")
        assert decor.isHidden() is True
        assert seal.isHidden() is True

        # 切回 ink 再次显示，验证可在主题间反复切换
        apply_theme("ink")
        assert decor.isHidden() is False
    finally:
        theme.set_ink_decor(None, None)
