# RimeConfig 0.2.0 · 主题皮肤设计规格

> 形态：Windows 桌面 GUI（Python + PySide6 + QSS）｜ 目标：3 套可切换完整皮肤（配色 + 字体 + 装饰）
> 本轮仅交付设计规格，不接入任何功能模块；QSS 为骨架，供 `theme.py` 机制落地。

---

## 1. 共享基础（三套皮肤一致）

布局、间距、圆角、组件尺寸在所有皮肤中保持不变，仅换「令牌值」，保证切换时窗口结构零抖动。

| 维度 | 取值 | 说明 |
|---|---|---|
| 窗口默认尺寸 | 1180 × 760 | 与真实 PySide6 窗口一致 |
| 标题栏高 | 44px | 含最小/关闭→托盘 |
| 左侧导航宽 | 208px | 导航项 4 个 + 底部状态 |
| 内容区左右内边距 | 22px（A/B） / 26px（C 含左侧装饰条） | C 多 3px 渐变装饰条 |
| 工具栏控件高 | 36px | 搜索框 / 按钮 / 下拉 |
| 表格行高 | 44px | 汉字｜拼音｜权重｜分组｜操作 |
| 状态栏高 | 34px | 计数 + 保存态 |
| 间距基准 | 4px | 4 / 8 / 12 / 16 / 24 |
| 圆角 | 控件 8 · 卡片 10 · 胶囊 20 · 标题栏按钮 6 | 全局统一 |
| 字号 | 标题 20 · 副标题 12 · 正文 13 · 表头 12 · 导航 13.5 · 拼音等宽 12.5 | 固定比例 |

---

## 2. 三套主题令牌对照

> 命名约定：`--bg-*` 背景、`--text-*` 文字、`--accent` 主色、`--border*` 描边、`--conflict*` 冲突高亮、`--success*` 成功态。
> QSS 不支持 CSS 变量；落地见第 5 节（占位符替换 或 三份独立 QSS）。

### A · Fluent Light（类 Windows 11 浅色）

| Token | 值 | 用途 |
|---|---|---|
| `--bg-app` | `#F3F3F3` | 应用主背景 |
| `--bg-surface` | `#FFFFFF` | 卡片 / 表格 |
| `--bg-sidebar` | `#FAFAFA` | 左侧导航 |
| `--bg-elevated` | `#FFFFFF` | 输入框 / 下拉 |
| `--bg-hover` | `#EDEDED` | 导航 / 行 hover |
| `--bg-active-nav` | `#E6F0F9` | 当前导航项 |
| `--border` | `#E5E5E5` | 分隔线 |
| `--border-strong` | `#D8D8D8` | 输入框描边 |
| `--text-primary` | `#1B1B1B` | 主文字 |
| `--text-secondary` | `#5A6066` | 次级文字（4.5:1+） |
| `--text-muted` | `#8A8A8A` | 占位 / 极弱 |
| `--accent` | `#185FA5` | 主色（品牌蓝） |
| `--accent-hover` | `#14507F` | 主色 hover |
| `--accent-soft` | `#E6F0F9` | 主色浅底 |
| `--on-accent` | `#FFFFFF` | 主色上的文字 |
| `--success-text` | `#1E7E45` | 成功文字 |
| `--success-bg` | `#EAF4EE` | 成功胶囊底 |
| `--conflict-bg` | `#FFF7E6` | 冲突行底 |
| `--conflict-border` | `#F2D69A` | 冲突描边 |
| `--conflict-text` | `#B26A00` | 冲突文字 |
| `--font-ui` | `"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif` | 界面正文 |
| `--font-heading` | 同 `--font-ui`（无衬线） | 页面标题 |
| `--font-mono` | `"Cascadia Code","Consolas",monospace` | 拼音 / 编码 |

### B · Dark Pro（炭灰深色）

| Token | 值 | 用途 |
|---|---|---|
| `--bg-app` | `#1A1A1A` | 应用主背景 |
| `--bg-surface` | `#202020` | 卡片 / 表格 |
| `--bg-sidebar` | `#1E1E1E` | 左侧导航 |
| `--bg-elevated` | `#232323` | 输入框 / 下拉 |
| `--bg-hover` | `#272727` | 导航 / 行 hover |
| `--bg-active-nav` | `#243245` | 当前导航项（蓝灰底） |
| `--border` | `#2E2E2E` | 分隔线 |
| `--border-strong` | `#33373B` | 输入框描边 |
| `--text-primary` | `#ECECEC` | 主文字 |
| `--text-secondary` | `#9AA0A6` | 次级文字 |
| `--text-muted` | `#74797F` | 占位 / 极弱 |
| `--accent` | `#4A9EFF` | 主色（亮蓝） |
| `--accent-hover` | `#62ACFF` | 主色 hover |
| `--accent-soft` | `#243245` | 主色浅底 |
| `--on-accent` | `#0E0E0E` | 主色上的文字（深字） |
| `--success-text` | `#5FD08A` | 成功文字 |
| `--success-bg` | `#1C2C22` | 成功胶囊底 |
| `--conflict-bg` | `#2E2519` | 冲突行底 |
| `--conflict-border` | `#6B4F26` | 冲突描边 |
| `--conflict-text` | `#E0A030` | 冲突文字（暖金） |
| `--font-ui` | 同 A | 界面正文 |
| `--font-heading` | 同 A | 页面标题 |
| `--font-mono` | 同 A | 拼音 / 编码 |

### C · Ink Scholar（水墨 · 暖纸）

| Token | 值 | 用途 |
|---|---|---|
| `--bg-app` | `#F5F1E8` | 应用主背景（米纸） |
| `--bg-surface` | `#FBF8F0` | 卡片 / 表格 |
| `--bg-sidebar` | `#EBE2CF` | 左侧导航 |
| `--bg-elevated` | `#FBF8F0` | 输入框 / 下拉 |
| `--bg-hover` | `#F1E8D6` | 导航 / 行 hover |
| `--bg-active-nav` | `#2F5D50` | 当前导航项（黛青实底） |
| `--border` | `#D8CDB8` | 分隔线 |
| `--border-strong` | `#CDBE9F` | 输入框描边 |
| `--text-primary` | `#2B2620` | 主文字（墨） |
| `--text-secondary` | `#7A6E52` | 次级文字（4.5:1+） |
| `--text-muted` | `#A1916F` | 占位 / 极弱 |
| `--accent` | `#2F5D50` | 主色（黛青） |
| `--accent-hover` | `#264C41` | 主色 hover |
| `--accent-soft` | `#E3EFE8` | 主色浅底 |
| `--on-accent` | `#F3EFE4` | 主色上的文字 |
| `--seal` | `#B23A2E` | 朱砂（Logo 印章 / 冲突强调） |
| `--success-text` | `#2F5D50` | 成功文字 |
| `--success-bg` | `#E3EFE8` | 成功胶囊底 |
| `--conflict-bg` | `#F8E7E2` | 冲突行底（朱砂浅） |
| `--conflict-border` | `#E6B4A6` | 冲突描边 |
| `--conflict-text` | `#B23A2E` | 冲突文字（朱砂） |
| `--font-ui` | `"Microsoft YaHei","PingFang SC",sans-serif` | 界面正文 |
| `--font-heading` | `"Source Han Serif SC","Noto Serif SC","SimSun",serif` | 页面标题（**宋体**） |
| `--font-mono` | `"Cascadia Code","Consolas",monospace` | 拼音 / 编码 |
| 装饰 | 左侧 3px 渐变条（朱砂→黛青） + 朱砂印章 Logo | C 专属，仅该皮肤加载 |

---

## 3. 字体策略（完整皮肤包的关键）

- **A / B**：界面与标题均用无衬线（`Segoe UI` / `Microsoft YaHei`），与 Windows 原生一致。
- **C**：标题与品牌名用**宋体衬线**（`Source Han Serif SC`），正文仍用无衬线以保证可读性——这是 C 区别于 A/B 的核心气质来源，必须随皮肤一起切换，不能只换配色。
- 拼音 / 编码列统一等宽字体，三套不变。

---

## 4. 可访问性（WCAG AA）

| 检查项 | A | B | C | 结论 |
|---|---|---|---|---|
| 主文字 / 背景对比 | ~15:1 | ~16:1 | ~12:1 | ✅ 远超 4.5:1 |
| 次级文字 / 背景 | 5.8:1 | 8:1 | 5:1 | ✅ ≥ 4.5:1 |
| 主色按钮文字 | 白/#185FA5 7:1 | 黑/#4A9EFF 5.5:1 | 浅/#2F5D50 8:1 | ✅ |
| 冲突文字 / 底色 | #B26A00/#FFF7E6 4.8:1 | #E0A030/#2E2519 6:1 | #B23A2E/#F8E7E2 4.8:1 | ✅ |
| 焦点指示 | 全局 2px 实线 outline | 同 | 同 | ✅ |

> 注：对比为设计期估算，最终以 Windows 显示环境实测为准（项目已有沙盒，无法覆盖像素级观感）。

---

## 5. QSS 落地建议（供 theme.py）

项目已有 `theme.py`（`apply_theme()` 作用于 `QApplication`）+ `application.qss` / `application_dark.qss` + 设置页「主题」下拉（值 `light`/`dark`）。扩展为三套有两种落地方式：

**方式一（推荐）：单模板 + 占位符替换**
- 一份 `application.qss` 模板，色值写成占位符如 `@ACCENT@`、`@BG_APP@`、`@FONT_HEADING@`。
- `theme.py` 内置三套令牌字典，切换时读取对应字典做字符串替换后再 `setStyleSheet`。
- 好处：改一处全局生效，避免三份文件漂移；C 的字体与装饰也作为令牌注入。

**方式二：三份独立 QSS**
- `application_light.qss` / `application_dark.qss`（刷新现有）/ `application_ink.qss`（新增）。
- `build.spec` 的 `datas` 登记三份；`theme.py` 按当前主题选文件。
- 与现有结构最贴合，但维护成本随皮肤数线性增长。

**设置页「主题」下拉扩展**
- 选项由 `浅色 / 深色` 扩展为 `浅色(A) / 深色(B) / 水墨(C)`，内部值 `light` / `dark` / `ink`。
- `themeChanged` 信号驱动即时切换并持久化（沿用现有机制，仅重设样式、不重建对象、不丢表单、不保存部署）。

---

## 6. 设置页主题切换交互（见 `settings_theme_switcher.png`）

- 「主题」项改为**三选项卡片式单选**：每张卡片含迷你预览缩略 + 名称 + 当前选中勾选。
- 选中即实时预览（调用 `themeChanged`），无需点「保存」即生效，符合现有「切换即时生效」行为。
- 卡片预览分别展示该皮肤的导航条 + 主色块 + 标题字体样例，让用户一眼区分 A/B/C 气质。

---

**UI Designer 交付物清单**
- `TOKENS.md`（本文件）：令牌表 + 字体 + 可访问性 + 落地建议
- `application_light.qss` / `application_dark.qss` / `application_ink.qss`：三套 QSS 骨架
- `settings_theme_switcher.png`：设置页主题切换界面稿
