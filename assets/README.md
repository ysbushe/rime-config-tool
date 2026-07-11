# assets 目录

放置应用图标 `app.ico`（Windows 可执行文件图标）。

- `build.spec` 已做优雅降级：若 `app.ico` 不存在，则打包时不带图标（`icon=... if exists else None`）。
- `src/ui/application.qss` 由 PyInstaller 通过 `datas` 打入单文件 exe。

如需自定义图标，将 256×256（含多分辨率）的 `app.ico` 放在此目录即可。
