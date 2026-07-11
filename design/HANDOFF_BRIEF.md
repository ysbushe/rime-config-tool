# 任务交接指令 · RimeConfig 0.2.0 界面皮肤落地

> 以下为可直接转发给开发专家团的交接指令。完整规格见同目录 `HANDOFF.md`。

---

【任务】
将 RimeConfig 0.2.0 的界面设计系统落地为 PySide6 + QSS 实现，重点是**三套可切换完整皮肤**。

【背景】
设计阶段已完成并验收。现需把设计产物转成代码，不涉及业务功能逻辑，只做界面皮肤与组件样式。

【交付物位置】（均在 `rime-config-tool/design/` 下）
- `HANDOFF.md` —— 完整实现规格（必读）
- `themes/TOKENS.md` —— 三套皮肤令牌权威表（色值/字体/对比度）
- `themes/COMPONENTS.md` —— 各页面/弹窗/托盘/交互状态组件规格
- `themes/application_light.qss` / `application_dark.qss` / `application_ink.qss` —— 三套 QSS 骨架（改造基底）
- `index.html` + 17 张 `.png` —— 每屏/每种状态的像素参照（对着改）
- `concepts/*.png` —— 三套皮肤的整页气质参照

【目标】
实现三套皮肤：A 浅色(Fluent Light) / B 深色(Dark Pro) / C 水墨(Ink Scholar)。切换时**配色+字体+装饰整体切换**，即时生效，无重启。

【技术方案】
1. 采用「单模板 + 占位符替换」（推荐，见 HANDOFF.md §2）：
   - 把 QSS 骨架里的硬编码色值改成 `@TOKEN@` 占位符（如 `@BG_APP@`、`@ACCENT@`、`@FONT_HEADING@`）。
   - `theme.py` 内置 `THEME_TOKENS` 三套字典；`apply_theme(app, name)` 读模板 → 字符串替换 → `app.setStyleSheet(css)`。
2. 占位符清单与 A/B/C 取值见 `HANDOFF.md §1`（共 24 个，含 C 专属 `@SEAL@`、`@FONT_HEADING@`）。
3. 设置页「主题」下拉由 浅色/深色 扩为 **浅色(A) / 深色(B) / 水墨(C)**，内部值 `light` / `dark` / `ink`；选中即实时预览，沿用现有 `themeChanged` 持久化机制。
4. C 水墨专属装饰（切走必须清除）：左侧 3px 渐变条（朱砂→黛青）+ 朱砂印章 Logo + 宋体标题（`@FONT_HEADING@` 已覆盖 `QLabel#PageTitle`）。切到 light/dark 时移除，避免残留。

【硬性约束】
- 所有颜色/字体**只引用令牌**，禁止在业务代码里硬编码 `#185FA5` 之类。
- `apply_theme` **仅重设样式表**：不重建窗口对象、不丢表单数据、不触发任何保存/部署。
- 切换皮肤时窗口结构**零抖动**（只换色/字，无尺寸重排）。
- 打包：若走三文件方式，`build.spec` 的 `datas` 需登记 QSS；单模板方式仅一份 `application.qss`。

【QA 标准】
- 逐屏对照 `index.html` 总览画廊，核对清单见 `HANDOFF.md §3`（全局布局 / 三套皮肤 / 符号页 / 方案页 / 设置页 / 弹窗 / 托盘 / 空态·加载态·批量态 / 无障碍）。
- 文字与背景对比 ≥ 4.5:1（WCAG AA），设计期估算见 TOKENS.md §4，最终以 Windows 实测为准。
- 焦点环、触控区、键盘可达性等无障碍项逐项过。

【完成判定】
三套皮肤在 Windows 10/11 下切换即时生效；视觉与 mockup 一致；无障碍达标；无控制台报错。

【对接】
令牌映射或组件细节有疑问，**找 UI Designer 澄清，不要自行改设计令牌值**。改动设计需回溯 HANDOFF.md 与 TOKENS.md。

---
（本指令配套文件：`HANDOFF.md`、`TOKENS.md`、`COMPONENTS.md`、`application_*.qss`、`index.html` 及 PNG 资源，均位于 `rime-config-tool/design/`）
