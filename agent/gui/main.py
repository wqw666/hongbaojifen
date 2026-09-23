#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 群红包监控 — 桌面客户端入口。"""

import os
import sys
from pathlib import Path


def _bootstrap_paths() -> None:
    """把 agent 根目录加入 sys.path，使 integration 包在两种运行方式下都可导入：
    1) 开发：run_dev.bat 会 cd gui 后 python main.py（本文件在 gui/ 下，根目录 = 上级）
    2) 打包：PyInstaller spec pathex 已含根目录（见 QQHongbaoMonitor.spec）
    """
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def main() -> None:
    _bootstrap_paths()
    if getattr(sys, "frozen", False):
        # PyInstaller onefile：bootloader 会把解包临时目录 _MEIxxxx 加进 PATH。
        # 子进程（wscript→cmd→NapCat→QQ）继承 PATH 后，加载 VCRUNTIME140.dll 等
        # 运行库时可能命中 _MEI 里的副本并一直占用 → agent 退出时 bootloader
        # 删不掉临时目录，每次关窗弹「Failed to remove temporary directory」。
        # 本进程的 DLL 在启动时已按全路径加载完，不需要 _MEI 在 PATH 里——剥掉即可；
        # 顺带把 CWD 钉到 exe 目录，杜绝子进程以临时目录为工作目录。
        cleaned = [p for p in os.environ.get("PATH", "").split(os.pathsep) if "_MEI" not in p]
        os.environ["PATH"] = os.pathsep.join(cleaned)
        try:
            os.chdir(Path(sys.executable).resolve().parent)
        except OSError:
            pass
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少依赖", "请先安装: pip install customtkinter requests")
        sys.exit(1)

    # agent 只访问本机（NapCat webui / OneBot / 插件回调）与内网总后台。
    # 本机若开了系统代理（Steam++ / Clash 等），requests/urllib 默认会把
    # 127.0.0.1 也走代理 → NapCat 探测永远失败（环境未就绪）、二维码加载失败。
    # 全局强制绕过代理；play_tab/approve_tab 里无 session 的 requests.post 也靠这个兜底。
    os.environ["NO_PROXY"] = "*"

    from app.main_window import MainWindow

    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
