# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — 生成 agent.exe"""

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)
GUI = ROOT / "gui"
BUNDLE = ROOT / "plugin_bundle"

# 收集 customtkinter 主题资源（不 collect_submodules，避免分析过重）
try:
    from PyInstaller.utils.hooks import collect_data_files

    ctk_datas = collect_data_files("customtkinter")
except Exception:
    ctk_datas = []

datas = list(ctk_datas)
if BUNDLE.exists():
    datas.append((str(BUNDLE), "plugin_bundle"))

# 排除 GUI 未直接依赖的重型包，缩短 Analysis、避免打包环境中途被杀
excludes = [
    "numpy",
    "cryptography",
    "psutil",
    "chardet",
    "setuptools",
    "pkg_resources",
]

a = Analysis(
    [str(GUI / "main.py")],
    # GUI 之外还必须含根目录：顶层 integration 包（总后台对接）靠它进包
    pathex=[str(GUI), str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=["customtkinter", "requests", "urllib3", "certifi", "qrcode", "PIL", "PIL.Image"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
