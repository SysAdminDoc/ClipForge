# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


ROOT = Path(SPECPATH)
VERSION_RESOURCE = (
    str(ROOT / "packaging" / "windows-version.txt")
    if sys.platform == "win32"
    else None
)

a = Analysis(
    [str(ROOT / "clipforge.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (
            str(ROOT / "clipforge" / "assets" / "clipforge-mark-256.png"),
            "clipforge/assets",
        ),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt6.QtWebEngine'],
    noarchive=False,
    optimize=0,
)

# PyQt6 resolves Windows' ICU compatibility DLL at runtime. A developer PATH can
# expose an unrelated ICU build that PyInstaller would otherwise bundle first.
if sys.platform == "win32":
    system_icu_dlls = {"icuuc.dll", "icudt78.dll"}
    a.binaries = [
        entry
        for entry in a.binaries
        if Path(entry[0]).name.casefold() not in system_icu_dlls
    ]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ClipForge',
    icon=str(ROOT / "packaging" / "clipforge.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    version=VERSION_RESOURCE,
    codesign_identity=None,
    entitlements_file=None,
)
