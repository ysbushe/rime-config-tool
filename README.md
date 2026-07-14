# RIME 配置小工具（RimeConfig）

面向 Windows 小狼毫的本地桌面配置工具，使用 PySide6 开发。程序主要管理：

- `custom_phrase.txt`：自定义短语、编码与权重
- `rime_frost.schema.yaml`：方案信息只读检测
- `symbols_v.yaml`：符号分类与条目

程序离线运行，支持系统托盘、全局热键、写前备份和小狼毫重新部署。

## 功能

- 词库增删改查、分组、搜索和排序，默认按加入顺序倒序显示。
- 全拼、严格简拼、紧凑简拼和混剪简拼建议，可点击选用。
- 使用 `'` 显示拼音边界；边界不会写入 RIME 词库编码。
- 中文弯引号、全角引号和反引号自动更正为显示分隔符。
- 同码候选检测、权重调整及“仅重码”快速视图。
- 中英混合词组和连续数字的编码建议。
- `pinyin_display.ini` 保存人工显示边界，并随短语词库备份、恢复。
- 自定义备份目录，支持三个受管文件的版本恢复。
- 沙盒预览模式，所有修改发生在程序副本中。
- 可配置全局热键、自动部署、开机自启和浅色/深色/水墨主题。

## 环境

- Windows 10/11
- Python 3.13（建议使用项目虚拟环境）
- 小狼毫，可选 `WeaselDeployer.exe` 自动部署

## 开发运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m src.main
```

首次启动会依次通过注册表、`%APPDATA%\Rime` 和 `RIME_USER_DIR` 探测 RIME 用户目录，也可在设置页手动指定。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试使用临时 RIME 目录和 Qt offscreen 平台，不应修改真实词库或程序设置。

## 打包

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean build.spec
```

产物为 `dist\RimeConfig.exe`，单文件、无控制台窗口。若提供 `assets\app.ico`，打包时会使用该图标；否则使用程序内置图标。

## 数据与备份

- 词库始终以 UTF-8 无 BOM、Tab 分隔格式写入：`文本<Tab>编码<Tab>权重`。
- `.bak` 只是备份扩展名，恢复时内容会复制回原文件名。
- 默认备份目录为 `<Rime目录>\.backup`，也可在设置页自定义。
- 程序设置保存在用户配置目录；备份路径同时记录于 `backup.ini`。
- `custom_phrase.txt.groups.json` 和 `pinyin_display.ini` 是程序 sidecar，不改变 RIME 原生词库格式。

## 项目结构

```text
src/
  config/      路径探测和字段映射
  encoding/    编码建议与显示边界推导
  repo/        词库、方案和符号数据访问
  service/     备份、拼音、部署、热键、沙盒和分组
  ui/          PySide6 主窗口、页面和弹窗
tests/         自动化测试及隔离 fixtures
design/        界面设计资料
docs/          产品和交接文档
```

## 发布说明

当前版本：`0.4.0`。本地发布说明见 `docs/RELEASE_NOTES.md`。上传前请确认测试通过，并避免提交 `.venv/`、`build/`、`dist/`、缓存及运行日志；这些路径已写入 `.gitignore`。
