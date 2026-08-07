# -*- mode: python ; coding: utf-8 -*-
# Produces two process-isolated executables in dist/.
import os

icon_path = os.path.join("assets", "app.ico")
common_datas = [
    ("src/ui/application.qss", "src/ui"),
    ("src/ui/check.svg", "src/ui"),
    ("src/ui/check-light.svg", "src/ui"),
    ("src/ui/check-dark.svg", "src/ui"),
    ("src/ui/check-ink.svg", "src/ui"),
    ("src/ui/check-disabled.svg", "src/ui"),
    ("assets", "assets"),
]
common_hiddenimports = [
    "src", "src.config", "src.utils", "src.encoding", "src.repo",
    "src.service", "src.service.hotkey_backends", "src.ui", "pypinyin", "yaml",
]

main_a = Analysis(
    ["src/main.py"], pathex=[os.getcwd()], binaries=[], datas=common_datas,
    hiddenimports=common_hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=[], noarchive=False,
)
main_pyz = PYZ(main_a.pure)
main_exe = EXE(
    main_pyz, main_a.scripts, main_a.binaries, main_a.datas, [], name="RimeConfig",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    upx_exclude=[], runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon=icon_path if os.path.exists(icon_path) else None,
)

diag_a = Analysis(
    ["src/diagnostics_main.py"], pathex=[os.getcwd()], binaries=[], datas=common_datas,
    hiddenimports=["src", "src.config", "src.plugins", "src.service", "src.ui"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
diag_pyz = PYZ(diag_a.pure)
diag_exe = EXE(
    diag_pyz, diag_a.scripts, diag_a.binaries, diag_a.datas, [],
    name="RimeInputDiagnostics", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, icon=icon_path if os.path.exists(icon_path) else None,
)