# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — NetNeighbor 2.0 (PySide6). Entry point: main.py.

from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).resolve().parent.parent


def _read_version(version_file: Path) -> tuple[tuple[int, int, int, int], str]:
    ver_str = version_file.read_text(encoding="utf-8").strip()
    if "#" in ver_str:
        ver_str = ver_str.split("#", 1)[0].strip()
    parts = [int(x) for x in ver_str.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4]), ver_str


_ver_tuple, _ver_str = _read_version(ROOT / "VERSION")
_version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_ver_tuple,
        prodvers=_ver_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Luc LEBOSSE"),
                        StringStruct("FileDescription", "NetNeighbor"),
                        StringStruct("FileVersion", _ver_str),
                        StringStruct("InternalName", "NetNeighbor"),
                        StringStruct(
                            "LegalCopyright",
                            "Copyright (C) Luc LEBOSSE. Licensed under LGPL-3.0-or-later.",
                        ),
                        StringStruct("OriginalFilename", "NetNeighbor.exe"),
                        StringStruct("ProductName", "NetNeighbor"),
                        StringStruct("ProductVersion", _ver_str),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
_version_info_path = Path(SPECPATH) / "_version_info.txt"
_version_info_path.write_text(str(_version_info), encoding="utf-8")

_entry = ROOT / "main.py"

block_cipher = None

_datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "locale"), "locale"),
    (str(ROOT / "data"), "data"),
    (str(ROOT / "config"), "config"),
    (str(ROOT / "VERSION"), "."),
]

_hiddenimports = [
    "zeroconf",
    "wsdiscovery",
    "wsdiscovery.discovery",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
]

a = Analysis(
    [str(_entry)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NetNeighbor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icons" / "netneighbor.ico")
    if (ROOT / "assets" / "icons" / "netneighbor.ico").is_file()
    else None,
    version=str(_version_info_path),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="NetNeighbor",
)
