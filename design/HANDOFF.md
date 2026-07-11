# RimeConfig 0.2.0 · 实现交接清单（Design → Dev Handoff）

> **用途**：UI 设计师 → 开发专家团的落地交接文档。设计产物已就绪，本文把"设计令牌如何变成代码、切换机制怎么接、落地后怎么对照设计做 QA"说清楚，目标是**降低开发返工**。
> **配套阅读**：`design/themes/TOKENS.md`（令牌表）、`design/themes/COMPONENTS.md`（组件规格）、`design/themes/*.qss`（骨架）、`design/index.html`（总览画廊，17 张 mockup 对照）。
> **范围**：仅界面皮肤与组件样式落地，**不含业务功能逻辑**。

---

## 0. 交接物清单（直接给开发团）

| 文件 | 类型 | 用途 |
|---|---|---|
| `design/themes/TOKENS.md` | 规格 | 三套皮肤令牌值 + 字体 + WCAG AA 对比 + 落地建议 |
| `design/themes/COMPONENTS.md` | 规格 | 各页面/弹窗/托盘/交互状态组件尺寸·状态·令牌继承 |
| `design/themes/application_light.qss` | QSS 骨架 | A 浅色（可改造成模板或独立文件） |
| `design/themes/application_dark.qss` | QSS 骨架 | B 深色 |
| `design/themes/application_ink.qss` | QSS 骨架 | C 水墨（含宋体/印章/装饰条注入点） |
| `design/index.html` + 17 张 `.png` | 视觉对照 | 每屏/每种状态的像素参照 |
| `design/concepts/*.png` | 视觉方向 | 三套皮肤的整页气质参照 |

---

## 1. 令牌 → QSS 映射（核心）

QSS **不支持 CSS 变量**。落地采用 `TOKENS.md` 第 5 节**方式一（推荐）：单模板 + 占位符替换**。

### 1.1 占位符总表

把三套 QSS 骨架里的硬编码色值，统一替换为下方占位符。`theme.py` 持三套字典，切换时做字符串替换后 `setStyleSheet`。

| 占位符 | 含义 | A(Fluent Light) | B(Dark Pro) | C(Ink Scholar) |
|---|---|---|---|---|
| `@BG_APP@` | 应用主背景 | `#F3F3F3` | `#1A1A1A` | `#F5F1E8` |
| `@BG_SURFACE@` | 卡片/表格底 | `#FFFFFF` | `#202020` | `#FBF8F0` |
| `@BG_SIDEBAR@` | 左侧导航底 | `#FAFAFA` | `#1E1E1E` | `#EBE2CF` |
| `@BG_ELEVATED@` | 输入框/下拉底 | `#FFFFFF` | `#232323` | `#FBF8F0` |
| `@BG_HOVER@` | 导航/行 hover | `#EDEDED` | `#272727` | `#F1E8D6` |
| `@BG_ACTIVE_NAV@` | 当前导航项底 | `#E6F0F9` | `#243245` | `#2F5D50` |
| `@BORDER@` | 分隔线 | `#E5E5E5` | `#2E2E2E` | `#D8CDB8` |
| `@BORDER_STRONG@` | 输入框描边 | `#D8D8D8` | `#33373B` | `#CDBE9F` |
| `@TEXT_PRIMARY@` | 主文字 | `#1B1B1B` | `#ECECEC` | `#2B2620` |
| `@TEXT_SECONDARY@` | 次级文字 | `#5A6066` | `#9AA0A6` | `#7A6E52` |
| `@TEXT_MUTED@` | 占位/极弱 | `#8A8A8A` | `#74797F` | `#A1916F` |
| `@ACCENT@` | 主色 | `#185FA5` | `#4A9EFF` | `#2F5D50` |
| `@ACCENT_HOVER@` | 主色 hover | `#14507F` | `#62ACFF` | `#264C41` |
| `@ACCENT_SOFT@` | 主色浅底 | `#E6F0F9` | `#243245` | `#E3EFE8` |
| `@ON_ACCENT@` | 主色上文字 | `#FFFFFF` | `#0E0E0E` | `#F3EFE4` |
| `@SUCCESS_TEXT@` | 成功文字 | `#1E7E45` | `#5FD08A` | `#2F5D50` |
| `@SUCCESS_BG@` | 成功胶囊底 | `#EAF4EE` | `#1C2C22` | `#E3EFE8` |
| `@CONFLICT_BG@` | 冲突行底 | `#FFF7E6` | `#2E2519` | `#F8E7E2` |
| `@CONFLICT_BORDER@` | 冲突描边 | `#F2D69A` | `#6B4F26` | `#E6B4A6` |
| `@CONFLICT_TEXT@` | 冲突文字 | `#B26A00` | `#E0A030` | `#B23A2E` |
| `@SEAL@` | 朱砂（仅 C） | — | — | `#B23A2E` |
| `@FONT_UI@` | 界面字体 | `"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif` | 同 A | `"Microsoft YaHei","PingFang SC",sans-serif` |
| `@FONT_HEADING@` | 标题字体 | 同 `@FONT_UI` | 同 `@FONT_UI` | `"Source Han Serif SC","Noto Serif SC","SimSun",serif` |
| `@FONT_MONO@` | 拼音/编码等宽 | `"Cascadia Code","Consolas",monospace` | 同 A | 同 A |

> **注意**：C 的 `@SEAL@` 与 `@FONT_HEADING@` 是 C 专属；A/B 模板里出现 `@SEAL@` 的位置直接渲染为空或跳过（见 §2.3）。

### 1.2 骨架改造示意

`application_light.qss` 当前是硬编码值。改为模板：

```css
/* 改造前 */ background: #185FA5;
/* 改造后 */ background: @ACCENT@;
/* 改造前 */ font-family: "Segoe UI",...;
/* 改造后 */ font-family: @FONT_UI@;
```

`theme.py` 侧：

```python
THEME_TOKENS = {
    "light": {"@ACCENT@": "#185FA5", "@BG_APP@": "#F3F3F3", ...},
    "dark":  {"@ACCENT@": "#4A9EFF", "@BG_APP@": "#1A1A1A", ...},
    "ink":   {"@ACCENT@": "#2F5D50", "@BG_APP@": "#F5F1E8", "@SEAL@": "#B23A2E", ...},
}

def apply_theme(app, name: str):
    tpl = read("application.qss")          # 单一模板
    css = tpl
    for ph, val in THEME_TOKENS[name].items():
        css = css.replace(ph, val)
    # C 专属：注入装饰（见 §2.3）
    if name == "ink":
        css += INK_DECORATION
    app.setStyleSheet(css)
```

---

## 2. theme.py 切换机制

项目已有 `theme.py`（`apply_theme()` 作用于 `QApplication`）+ `application.qss` / `application_dark.qss` + 设置页「主题」下拉（值 `light`/`dark`）。扩展为三套：

### 2.1 推荐改造（方式一）
- 保留**一份** `application.qss` 模板（由现有 `application_light.qss` 改造为占位符版），删去 `application_dark.qss` 独立文件或保留为备份。
- `theme.py` 内置 `THEME_TOKENS` 三套字典（见 §1.1）。
- `apply_theme(app, "light"|"dark"|"ink")`：读模板 → 替换 → `setStyleSheet`。

### 2.2 设置页下拉扩展
- 选项：`浅色(A)` / `深色(B)` / `水墨(C)`，内部值 `light` / `dark` / `ink`。
- `themeChanged` 信号驱动即时切换并持久化（沿用现有机制）。
- **关键约束**：仅重设样式表、不重建窗口对象、不丢表单数据、不触发任何保存/部署（与现有"切换即时生效"行为一致）。

### 2.3 C 水墨专属装饰注入
C 皮肤需额外加载（切走移除），由 `theme.py` 在 `name=="ink"` 时追加：
- 左侧 3px 渐变条（朱砂→黛青）：在 `QFrame#ContentFrame` 左侧加 `border-left` 或独立装饰 Widget。
- 朱砂印章 Logo：替换品牌区 Logo 为印章样式（可用 `QLabel` + 圆角红底白字「藏」或项目名首字）。
- 标题宋体：`@FONT_HEADING@` 已覆盖所有页面标题 `QLabel#PageTitle`。
- 这些装饰**只在 ink 时存在**，切到 light/dark 必须清除，避免残留。

### 2.4 备选方式（方式二：三份独立 QSS）
若团队更想贴合现有"按文件选"结构：`application_light.qss` / `application_dark.qss` / `application_ink.qss` 三份齐备（已提供骨架），`theme.py` 按当前主题选文件 `setStyleSheet`。代价：维护成本随皮肤数线性增长，易漂移。**仍推荐方式一。**

---

## 3. 逐页 / 组件 设计 QA 核对表

> 落地后逐项对照 `design/index.html` 对应 PNG。✅ = 通过。开发自测用。

### 3.1 全局布局（三套一致，§TOKENS 第 1 节）
- [ ] 窗口 1180×760；标题栏 44px；左侧导航 208px
- [ ] 工具栏控件高 36px；表格行高 44px；状态栏 34px
- [ ] 圆角：控件 8 / 卡片 10 / 胶囊 20 / 标题栏按钮 6
- [ ] 间距基准 4px，节奏 4/8/12/16/24
- [ ] 切换皮肤时**窗口结构零抖动**（仅换色/字，无尺寸重排）

### 3.2 三套皮肤对照
- [ ] A：品牌蓝 `#185FA5` 主色；导航选中浅蓝底 `#E6F0F9`
- [ ] B：炭灰底 `#1A1A1A`；主色亮蓝 `#4A9EFF`；`--on-accent` 用深字 `#0E0E0E`
- [ ] C：米纸 `#F5F1E8`；黛青 `#2F5D50` 主色；标题宋体生效；左侧渐变条 + 朱砂印章可见
- [ ] 三套主文字/背景对比 ≥ 4.5:1（见 TOKENS 第 4 节，实测为准）

### 3.3 符号表页（symbols_page / symbols_page_ink）
- [ ] 左侧分类列表项高 ~38px 圆角 7；选中 `--bg-active-nav` + `--accent` 文字
- [ ] 符号芯片 30×30 圆角 6，`--bg-elevated` 底 + `--border` 描边
- [ ] 表格列：键位(等宽)/符号(芯片组)/分类(标签)/操作
- [ ] 未保存态状态栏「● 有未保存的修改」用 `--conflict-text`
- [ ] C：宋体标题 + 印章 Logo + 左侧渐变条

### 3.4 方案配置页（schema_page / schema_page_dark）
- [ ] 信息卡头像 52×52 圆角 12，渐变 `--accent`→亮色
- [ ] 复选框 20×20 圆角 5；未选 `--border-strong`；选中 `--accent` 底白勾；半选减号；禁用降透明
- [ ] 开关 Toggle 38×22 圆角 20；开 `--accent`、关 `#D0D0D0`(浅)/`#3A3A3A`(深)
- [ ] 按键条目右侧键位芯片（等宽 `--bg-elevated` 底）+ 「编辑」文字按钮 `--accent` hover

### 3.5 设置页（settings_page / settings_theme_switcher）
- [ ] 分区：常规 / 外观·主题 / 关于
- [ ] 常规行：标签(左) + 控件(右对齐)；路径输入框 280px
- [ ] 外观·主题：三选项卡片单选，选中打勾，选中即实时预览（无需点保存）
- [ ] 卡片预览含该皮肤导航条 + 主色块 + 标题字体样例

### 3.6 弹窗（quick_add_dialog / phrase_editor_dialog）
- [ ] 容器宽 440~460，白底圆角 12，阴影 0 20 50 rgba(0,0,0,.22)
- [ ] 标题栏高 46 底分隔；关闭 ✕ 右上
- [ ] 输入框高 36 圆角 8，聚焦 `--accent` 边 + 3px 光晕
- [ ] 快速收藏：文本/编码(默认全拼)/权重(下拉+自定义展开数字框 84px)/分组
- [ ] 词条编辑：汉字/拼音/权重/分组 + 错误条（`--conflict-text` 文字 + 浅底描边）
- [ ] 底部操作右对齐；主按钮 `--accent` 底白字，次按钮幽灵描边

### 3.7 系统托盘菜单（tray_menu）
- [ ] 容器宽 ~248 白底圆角 10 阴影 0 12 34
- [ ] 菜单项高 ~40 圆角 7，图标 16 + 文字 13；hover `--bg-hover`
- [ ] 子状态：右侧「开 ●」绿点 `--success-text`、「沙盒禁用」琥珀 `--conflict-text`
- [ ] 退出项危险红 `--conflict-text` hover 浅红底；分隔 1px `--border`

### 3.8 交互状态（state_empty / state_loading / state_batch）
- [ ] 空态：居中插画 +「词库还是空的」+「+ 新增词条」主按钮；工具栏保留可用；插画 `aria-label`
- [ ] 加载态：骨架行 shimmer 动画；工具栏 `disabled`；状态栏旋转 spinner +「加载中…」；尊重 `prefers-reduced-motion`
- [ ] 批量态：顶部批量条 `--accent-soft` 底「已选 N 项」；批量删除(红)/导出 CSV/取消；行复选框 20×20 选中 `--accent`；选中行 `--accent-soft` 底 + 左侧 3px `--accent` 条；批量条 `aria-live="polite"`

### 3.9 无障碍（全局）
- [ ] 焦点指示：全局 2px 实线 outline（键盘 Tab 可见）
- [ ] 交互元素触控区 ≥ 36~44px（按钮/复选框热区）
- [ ] 语义角色：复选框用原生 `QCheckBox`；弹窗 `setModal`；状态变更用 `aria-live`
- [ ] 文本缩放 200% 下不破版

---

## 4. 落地注意事项（踩坑预警）

1. **令牌只引用不写死**：任何组件颜色/字体都走占位符或令牌字典，禁止在业务代码里硬编码 `#185FA5` 之类。
2. **切换不重建对象**：`apply_theme` 只 `setStyleSheet`，否则会丢表单/打断操作。
3. **C 装饰要可逆**：注入的渐变条/印章/宋体，切走必须移除，否则 light/dark 会残留。
4. **打包登记**：若走方式二，记得在 `build.spec` 的 `datas` 登记 QSS 文件；方式一仅一份模板。
5. **字体兜底**：宋体 `Source Han Serif SC` 可能未安装，QSS 字体列表已带 `Noto Serif SC`→`SimSun` 兜底；代码层无需额外处理。
6. **对比度实测**：TOKENS 第 4 节对比为设计期估算，最终以 Windows 实际显示环境为准（项目沙盒不覆盖像素观感）。

---

**UI Designer 交接说明**：本文 + TOKENS.md + COMPONENTS.md + 三套 QSS 骨架 + 17 张 mockup 已构成完整设计交付。开发团按 §1 映射、§2 接机制、§3 做 QA 即可，预期设计返工率 < 10%。视觉以 `design/index.html` 总览画廊为准。
