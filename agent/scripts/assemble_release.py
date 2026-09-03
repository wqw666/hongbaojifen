#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 agent 执行器 release 交付包（exe + tools + 源码）。"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path


def strip_login_state(napcat_dir: Path) -> None:
    """删除 NapCat 副本中的本机登录态与密钥（每台部署机器需独立扫码登录）。"""
    targets = [
        napcat_dir / "cache" / "qrcode.png",
        *sorted(napcat_dir.glob("guild1.db*")),
        *sorted((napcat_dir / "config").glob("napcat_*.json")),
        *sorted((napcat_dir / "config").glob("onebot11_*.json")),
        napcat_dir / "config" / "passkey.json",
    ]
    for p in targets:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        except Exception:
            pass
    logs = napcat_dir / "logs"
    if logs.exists():
        try:
            shutil.rmtree(logs)
        except Exception:
            pass


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    release = root / "release"
    src_root = release / "agent"
    exe_name = "agent.exe"
    doc_name = "使用说明.txt"
    zip_name = "agent_交付包.zip"

    if release.exists():
        shutil.rmtree(release)

    for d in (
        release,
        release / "tools" / "NapCat",
        src_root / "gui" / "app",
        src_root / "plugin" / "src",
        src_root / "plugin" / "dist",
        src_root / "client",
        src_root / "plugin_bundle",
        src_root / "scripts",
        src_root / "integration",
    ):
        d.mkdir(parents=True, exist_ok=True)

    # exe
    dist_exe = next(root.glob("dist/*.exe"), None)
    if not dist_exe:
        print("ERROR: dist/*.exe not found")
        return 1
    shutil.copy2(dist_exe, release / exe_name)

    # 启动脚本
    for name in ("start_silent.bat", "一键启动.bat", "启动.vbs"):
        p = root / name
        if p.exists():
            shutil.copy2(p, release / name)

    # NapCat + 插件
    nap_src = root / "tools" / "NapCat"
    if not nap_src.exists():
        print("ERROR: tools/NapCat not found")
        return 1
    shutil.copytree(nap_src, release / "tools" / "NapCat", dirs_exist_ok=True)
    strip_login_state(release / "tools" / "NapCat")
    shutil.copy2(root / "tools" / "restart_napcat_hidden.vbs", release / "tools" / "restart_napcat_hidden.vbs")
    plugin_mjs = root / "plugin" / "dist" / "index.mjs"
    if plugin_mjs.exists():
        plug_dest = release / "tools" / "NapCat" / "plugins" / "napcat-plugin-cleaner"
        plug_dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_mjs, plug_dest / "index.mjs")

    # 源码
    for f in (root / "gui").glob("*.py"):
        shutil.copy2(f, src_root / "gui" / f.name)
    for f in (root / "gui" / "app").glob("*.py"):
        shutil.copy2(f, src_root / "gui" / "app" / f.name)
    for f in (root / "plugin" / "src").iterdir():
        if f.is_file():
            shutil.copy2(f, src_root / "plugin" / "src" / f.name)
    if plugin_mjs.exists():
        shutil.copy2(plugin_mjs, src_root / "plugin" / "dist" / "index.mjs")
    for name in ("package.json", "tsconfig.json", "vite.config.ts"):
        shutil.copy2(root / "plugin" / name, src_root / "plugin" / name)
    for f in (root / "integration").glob("*.py"):
        shutil.copy2(f, src_root / "integration" / f.name)
    shutil.copy2(root / "client" / "query.py", src_root / "client" / "query.py")
    bundle_dir = root / "plugin_bundle"
    if bundle_dir.exists():
        for f in bundle_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, src_root / "plugin_bundle" / f.name)
    for name in (
        "build.bat", "run_dev.bat", "requirements.txt", "README.md",
        "QQHongbaoMonitor.spec", "config.example.json",
    ):
        p = root / name
        if p.exists():
            shutil.copy2(p, src_root / name)
    for name in ("assemble_release.py", "write_release_doc.ps1"):
        p = root / "scripts" / name
        if p.exists():
            shutil.copy2(p, src_root / "scripts" / name)

    doc = """========================================
  agent — QQ 群红包监控执行器 使用说明
========================================

【解压后直接用】
  1. 解压整个文件夹到任意位置
  2. 双击「一键启动.bat」或「agent.exe」
  3. 软件内扫码登录 NapCat → 设置群号 → 部署插件 → 开始监控

【本目录文件】
  agent.exe            主程序
  一键启动.bat         推荐：自动启动 NapCat + 打开 GUI
  启动.vbs             无黑窗启动
  tools\\NapCat\\        内置 QQ 框架（必需，勿删）
  使用说明.txt         本文件
  agent\\                开发者源码（日常使用可忽略）

【首次配置】
  · 「QQ 扫码登录」→ 启动 NapCat → 手机 QQ 扫码
  · 「设置」填写要监听的群号 → 保存
  · 「部署插件并进入监控」
  · 「状态」页刷新连接，显示绿色即成功

【注意】
  · 仅支持 QQ 钱包红包
  · 每台机器独立扫码登录，不要复制其他机器的登录态
  · 同一 QQ 不要同时开两个 PC 客户端
  · 自动化存在封号风险，请合规使用

【二次开发】见 agent\\ 目录
========================================
"""
    (release / doc_name).write_text(doc, encoding="utf-8-sig")

    # zip（UTF-8 文件名）
    zip_path = root / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in release.rglob("*"):
            if p.is_file():
                arc = p.relative_to(release).as_posix()
                zf.write(p, arc)

    print(f"OK release: {release}")
    print(f"OK zip: {zip_path}")
    for p in sorted(release.iterdir()):
        print(f"  {p.name}{'/' if p.is_dir() else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
