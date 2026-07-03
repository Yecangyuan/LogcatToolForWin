# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ["src/logcat_tool_for_win/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/logcat_tool_for_win/resources/platform-tools", "platform-tools")],
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
    a.binaries,
    a.datas,
    [],
    name="logcat-tool-for-win",
    console=False,
    upx=False,
)
