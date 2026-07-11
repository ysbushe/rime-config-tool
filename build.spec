# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包脚本
# 用法：python -m PyInstaller --noconfirm --clean build.spec
# 产物：dist/RimeConfig.exe（单文件、无控制台窗口）
import os

app_name = "RimeConfig"
icon_path = os.path.join("assets", "app.ico")

a = Analysis(
    ["src/main.py"],
    pathex=[os.getcwd()],
    binaries=[],
    datas=[
        ("src/ui/application.qss", "src/ui"),
        ("src/ui/check.svg", "src/ui"),
        ("assets", "assets"),
    ],
    hiddenimports=[
        "src",
        "src.config",
        "src.utils",
        "src.encoding",
        "src.repo",
        "src.service",
        "src.service.hotkey_backends",
        "src.ui",
        "pypinyin",
        "yaml",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # --windowed：无控制台
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path if os.path.exists(icon_path) else None,
)
