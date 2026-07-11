"""三套皮肤（light/dark/ink）主题机制测试（基于临时副本，不碰真实 Rime 配置）。

覆盖：
    - apply_theme 三主题均能把模板占位符替换完（无 @XXX@ 残留）
    - 模板所用占位符均在 THEME_TOKENS 有定义
    - THEME_TOKENS 三套各含 28 令牌，A/B 的 @SEAL@ 为 ""
    - conflict_background() / accent_color() 返回当前主题值
    - ink 装饰在 ink 时显示、非 ink 隐藏
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import QFrame, QLabel

from src.ui import theme
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
    "@BG_ACTIVE_NAV@", "@BORDER@", "@BORDER_STRONG@", "@TEXT_PRIMARY@",
    "@TEXT_SECONDARY@", "@TEXT_MUTED@", "@ACCENT@", "@ACCENT_HOVER@",
    "@ACCENT_SOFT@", "@ON_ACCENT@", "@SUCCESS_TEXT@", "@SUCCESS_BG@",
    "@CONFLICT_BG@", "@CONFLICT_BORDER@", "@CONFLICT_TEXT@", "@SEAL@",
    "@SELECTION_BG@", "@SELECTION_TEXT@", "@TITLEBAR_BG@", "@TITLEBAR_TEXT@",
    "@FONT_UI@", "@FONT_HEADING@", "@FONT_MONO@",
]


def test_theme_tokens_count_and_seal() -> None:
    assert set(THEME_TOKENS.keys()) == {"light", "dark", "ink"}
    for name, tokens in THEME_TOKENS.items():
        assert len(tokens) == 28, f"{name} 应有 28 个令牌，实际 {len(tokens)}"
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


def test_apply_theme_replaces_all_placeholders(qapp) -> None:
    for name in ("light", "dark", "ink"):
        apply_theme(name)
        css = qapp.styleSheet()
        # 不应残留任何 @XXX@ 占位符
        for tk in _ALL_TOKENS:
            assert tk not in css, f"{name} 主题仍有未替换占位符 {tk}"
        # check.svg 必须替换为绝对 file URI
        assert "url(check.svg)" not in css
        assert "file://" in css


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
        assert seal.isHidden() is False

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
