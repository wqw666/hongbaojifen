#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ 群红包监控 — 桌面客户端入口。"""

import sys


def main() -> None:
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
