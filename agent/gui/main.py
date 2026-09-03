#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 群红包监控 — 桌面客户端入口。"""

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
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少依赖", "请先安装: pip install customtkinter requests")
        sys.exit(1)

    from app.main_window import MainWindow

    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
