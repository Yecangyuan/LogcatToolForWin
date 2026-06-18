# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["src/logcat_tool_for_win/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter"],
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
    [],
    exclude_binaries=True,
    name="logcat-tool-for-win",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="logcat-tool-for-win",
)
