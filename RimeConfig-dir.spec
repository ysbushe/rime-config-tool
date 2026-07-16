# -*- mode: python ; coding: utf-8 -*-
import os

app_name = "RimeConfig"
icon_path = os.path.join("assets", "app.ico")

a = Analysis(
    ['src\\main.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=[('src\\ui\\application.qss', 'src\\ui'), ('src\\ui\\check.svg', 'src\\ui'), ('src\\ui\\check-light.svg', 'src\\ui'), ('src\\ui\\check-dark.svg', 'src\\ui'), ('src\\ui\\check-ink.svg', 'src\\ui'), ('src\\ui\\check-disabled.svg', 'src\\ui'), ('assets', 'assets')],
    hiddenimports=['src', 'src.config', 'src.utils', 'src.encoding', 'src.repo', 'src.service', 'src.service.hotkey_backends', 'src.ui', 'pypinyin', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RimeConfig',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    icon=icon_path if os.path.exists(icon_path) else None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RimeConfig',
)
