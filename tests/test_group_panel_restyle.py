"""回归测试：主题切换后分组侧栏『全部』按钮主色随 @ACCENT@ 刷新。

Bug（第 2 轮回归）：切换主题后，GroupPanel 的『全部』按钮（_btn_all）主色不随主题
更新（如切到 ink 仍显示旧主题蓝 #185FA5）。根因：_btn_all 主色是显式 setStyleSheet
（值来自 accent_color() 即当前主题 @ACCENT@），需 GroupPanel.restyle() 重设；但主题
切换路径（MainWindow._on_theme_changed → apply_theme）此前未触发 restyle()。
QApplication.setStyleSheet 不会自动重算已显式设过样式表的控件，故按钮陈旧。

修复：MainWindow._on_theme_changed 在 apply_theme(theme) 之后调用
PhraseManager.restyle() → GroupPanel.restyle()，用新主题 accent_color() 重设 _btn_all
样式表。restyle 只重设样式表，不重建任何窗口对象、不丢表单数据。

不触碰真实 Rime 配置：
    - 轻量用例用空目录 GroupService（持久化禁用，绝不读写 %APPDATA%/Rime）；
    - 代理用例用临时目录的 app_context 构建真实 PhraseManager（auto_group_done=True
      防首次自动分组弹窗）。
"""
from __future__ import annotations

from src.service.group_service import GroupService
from src.ui.group_panel import GroupPanel
from src.ui.theme import accent_color, apply_theme


def _make_group_panel() -> GroupPanel:
    """用最小依赖构造 GroupPanel（空目录 GroupService，持久化禁用，不读写真实配置）。"""
    gs = GroupService("")  # 空字符串 → _enabled=False → 绝不触碰 %APPDATA%/Rime
    return GroupPanel(gs)


# --------------------------------------------------------------------------- #
# 核心回归：真实 GroupPanel + 真实 apply_theme + 真实 restyle
# --------------------------------------------------------------------------- #
def test_group_all_button_follows_light_then_ink(qapp) -> None:
    # light 主题：accent 应为 #185FA5
    apply_theme("light")
    assert accent_color() == "#185FA5"

    panel = _make_group_panel()
    # 初始构造基于 light，样式表应含主色 #185FA5（及 0.10 alpha 的叠加层）
    ss_light = panel._btn_all.styleSheet()
    assert "#185FA5" in ss_light

    # 切到 ink：accent 变为 #2F5D50（黛青）
    apply_theme("ink")
    assert accent_color() == "#2F5D50"

    # 切换前未 restyle：样式表仍陈旧（含 #185FA5），正是被修复前的状态
    # 核心断言：restyle 后样式表应含新主色 #2F5D50，且不再含旧蓝 #185FA5
    panel.restyle()
    ss_ink = panel._btn_all.styleSheet()
    assert "#2F5D50" in ss_ink
    assert "#185FA5" not in ss_ink


def test_group_all_button_follows_ink_then_dark(qapp) -> None:
    """反向切换同样成立：ink → dark 主色从 #2F5D50 变 #4A9EFF。"""
    apply_theme("ink")
    panel = _make_group_panel()
    assert "#2F5D50" in panel._btn_all.styleSheet()

    apply_theme("dark")
    assert accent_color() == "#4A9EFF"
    panel.restyle()
    ss = panel._btn_all.styleSheet()
    assert "#4A9EFF" in ss
    assert "#2F5D50" not in ss


# --------------------------------------------------------------------------- #
# 代理路径：PhraseManager.restyle() → GroupPanel.restyle() 真实布线
# --------------------------------------------------------------------------- #
def test_phrase_manager_restyle_propagates_theme(qapp, app_context) -> None:
    """经修复后的 PhraseManager.restyle() 代理，验证主色随主题刷新。

    用临时目录的 app_context 构建真实 PhraseManager（auto_group_done=True 防弹窗），
    模拟 MainWindow._on_theme_changed：apply_theme 后调 restyle。
    """
    apply_theme("light")
    ctx = app_context
    ctx.settings.auto_group_done = True  # 避免首次自动分组弹窗干扰
    from src.ui.phrase_manager import PhraseManager

    pm = PhraseManager(
        ctx.phrase_repo, ctx.group_service, ctx.backup_service,
        ctx.settings, ctx.deploy_service, ctx.pinyin_service,
    )
    assert "#185FA5" in pm._group_panel._btn_all.styleSheet()

    # 模拟 _on_theme_changed：apply_theme 之后 restyle
    apply_theme("ink")
    pm.restyle()

    ss = pm._group_panel._btn_all.styleSheet()
    assert "#2F5D50" in ss
    assert "#185FA5" not in ss
