"""主界面：状态 / 实时监控 / 历史查询 / 设置 / 帮助。"""

from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from typing import Callable

import customtkinter as ctk

from .api_client import ApiError, PluginClient
from .backend_tab import BackendTab
from .config_manager import AppConfig, find_napcat_launcher, load_config, save_config
from .napcat_paths import find_napcat_dir, qrcode_png_path
from .napcat_webui import NapCatWebUI
from .plugin_deploy import deploy_plugin
from .qr_image import load_qrcode_image, pil_to_ctk
from .query_export import write_query_csv

APP_VERSION = "2026.08.31-8"


def _is_startup_noise(err: Exception | str) -> bool:
    """NapCat/QQ 启动过程中的临时错误，不宜刷屏。"""
    msg = str(err).lower()
    keys = (
        "503",
        "service unavailable",
        "404",
        "10061",
        "connection refused",
        "未能建立",
        "无法连接",
        "napcat 未运行",
        "插件未连接",
    )
    return any(k in msg for k in keys)


class MainWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.title(f"agent 执行器  v{APP_VERSION}")
        self.geometry("980x680")
        self.minsize(860, 600)

        self.cfg = load_config()
        self.client = PluginClient(self.cfg)
        self._poll_job: str | None = None
        self._polling = False
        self._last_query_data: dict | None = None
        self._login_poll_job: str | None = None
        self._qr_ctk_image = None
        self._napcat_ready = False
        self._last_wait_log_at = 0.0
        self._selected_bill = ""
        self._filling_packets = False
        self._user_gen = 0

        self._build_ui()
        self._setup_tree_styles()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self.cfg.first_run_done:
            self.after(300, self._show_welcome)

        # 先探测连接；就绪后再开监控轮询，避免启动期 503/404 刷屏
        self.after(500, self.refresh_status)

    # ---------- UI ----------

    def _setup_tree_styles(self) -> None:
        """ttk Treeview 在 CustomTkinter 下选中行常不可见，强制高亮。"""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Monitor.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#1a1a1a",
            rowheight=28,
            borderwidth=0,
        )
        style.configure(
            "Monitor.Treeview.Heading",
            background="#E8EEF7",
            foreground="#1a1a1a",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Monitor.Treeview",
            background=[("selected", "#2563EB")],
            foreground=[("selected", "#FFFFFF")],
        )
        style.configure(
            "Detail.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#1a1a1a",
            rowheight=26,
        )
        style.configure(
            "Detail.Treeview.Heading",
            background="#E8EEF7",
            foreground="#1a1a1a",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Detail.Treeview",
            background=[("selected", "#2563EB")],
            foreground=[("selected", "#FFFFFF")],
        )
        self.tree_packets.configure(style="Monitor.Treeview")
        self.tree_claims.configure(style="Detail.Treeview")
        self.tree_members.configure(style="Detail.Treeview")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text="agent 执行器", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.lbl_conn = ctk.CTkLabel(header, text="连接: 检测中…", text_color="gray")
        self.lbl_conn.grid(row=0, column=1, sticky="e", padx=8)
        ctk.CTkButton(header, text="启动 NapCat", width=100, command=self.launch_napcat).grid(
            row=0, column=2, sticky="e", padx=(0, 4)
        )
        ctk.CTkButton(header, text="刷新连接", width=100, command=self.refresh_status).grid(
            row=0, column=3, sticky="e"
        )

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=12)
        self.tab_login = self.tabs.add("QQ 扫码登录")
        self.tab_status = self.tabs.add("状态")
        self.tab_monitor = self.tabs.add("实时监控")
        self.tab_query = self.tabs.add("历史查询")
        self.tab_settings = self.tabs.add("设置")
        self.tab_backend = self.tabs.add("总后台对接")
        self.tab_help = self.tabs.add("使用说明")

        self._build_login_tab()
        self._build_status_tab()
        self._build_monitor_tab()
        self._build_query_tab()
        self._build_settings_tab()
        self._build_backend_tab()
        self._build_help_tab()

    def _build_login_tab(self) -> None:
        f = self.tab_login
        f.grid_columnconfigure(0, weight=1)
        f.grid_columnconfigure(1, weight=1)
        f.grid_rowconfigure(1, weight=1)

        tip = ctk.CTkLabel(
            f,
            text="首次使用：1) 点「启动 NapCat」  2) 用手机 QQ 扫下方二维码  3) 登录成功后自动跳转",
            text_color="gray",
            wraplength=900,
            justify="left",
        )
        tip.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 4))

        left = ctk.CTkFrame(f)
        left.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=8)
        ctk.CTkLabel(left, text="登录二维码", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(12, 8))
        self.lbl_qr = ctk.CTkLabel(left, text="等待二维码…", width=280, height=280)
        self.lbl_qr.pack(padx=16, pady=8)
        qr_btns = ctk.CTkFrame(left, fg_color="transparent")
        qr_btns.pack(pady=8)
        ctk.CTkButton(qr_btns, text="刷新二维码", command=self.refresh_login_qrcode).pack(pady=4)
        ctk.CTkButton(qr_btns, text="启动 NapCat", command=self.launch_napcat).pack(pady=4)

        right = ctk.CTkFrame(f)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=8)
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text="登录状态", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(12, 8)
        )
        self.lbl_login_status = ctk.CTkLabel(
            right, text="未检测", text_color="#e67e22", wraplength=360, justify="left"
        )
        self.lbl_login_status.grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.lbl_login_uin = ctk.CTkLabel(right, text="", wraplength=360, justify="left")
        self.lbl_login_uin.grid(row=2, column=0, sticky="w", padx=16, pady=4)

        ctk.CTkLabel(right, text="快捷登录（本机曾登录过的 QQ）", text_color="gray").grid(
            row=3, column=0, sticky="w", padx=16, pady=(16, 4)
        )
        self.frame_quick_login = ctk.CTkScrollableFrame(right, height=160)
        self.frame_quick_login.grid(row=4, column=0, sticky="nsew", padx=12, pady=4)
        right.grid_rowconfigure(4, weight=1)

        ctk.CTkButton(
            right, text="登录成功后：部署插件并进入监控", command=self._after_login_setup
        ).grid(row=5, column=0, sticky="w", padx=16, pady=16)

    def _napcat_vbs_path(self):
        napcat = find_napcat_dir(self.cfg)
        if napcat:
            vbs = napcat.parent / "restart_napcat_hidden.vbs"
            if vbs.is_file():
                return vbs
        for root_fn in (find_napcat_launcher(),):
            if root_fn and root_fn.parent.name == "tools":
                vbs = root_fn.parent / "restart_napcat_hidden.vbs"
                if vbs.is_file():
                    return vbs
        return None

    def launch_napcat(self, quick_uin: str = "") -> None:
        vbs = self._napcat_vbs_path()
        if not vbs:
            launcher = find_napcat_launcher()
            if launcher:
                try:
                    os.startfile(str(launcher))
                    self._log_status(f"正在启动（{launcher.name}）…")
                    self.lbl_conn.configure(text="正在启动 NapCat…", text_color="#e67e22")
                    self._schedule_login_poll()
                    return
                except OSError as e:
                    messagebox.showerror("启动失败", str(e))
                    return
            messagebox.showerror("未找到 NapCat", "请确认安装包内包含 tools\\NapCat 目录")
            return
        try:
            cmd = ["wscript", "//nologo", str(vbs)]
            if quick_uin:
                cmd.append(str(quick_uin))
            # 启动期间先停监控轮询，避免 503/404 刷屏
            self._napcat_ready = False
            self._polling = False
            if self._poll_job:
                try:
                    self.after_cancel(self._poll_job)
                except Exception:
                    pass
                self._poll_job = None
            subprocess.Popen(cmd, cwd=str(vbs.parent))
            self._log_status("正在后台启动 NapCat（不会关闭您已打开的 QQ）…")
            self.lbl_conn.configure(text="正在启动 NapCat…", text_color="#e67e22")
            self.lbl_login_status.configure(text="正在启动 NapCat，约 15~60 秒后可扫码", text_color="#e67e22")
            self.tabs.set("QQ 扫码登录")
            self._schedule_login_poll()
            self.after(8000, self.refresh_login_qrcode)
            self.after(20000, self.refresh_login_qrcode)
            self.after(25000, self.refresh_status)
        except OSError as e:
            messagebox.showerror("启动失败", str(e))

    def _schedule_login_poll(self) -> None:
        if self._login_poll_job:
            self.after_cancel(self._login_poll_job)
        self._login_poll_tick()

    def _login_poll_tick(self) -> None:
        self.refresh_login_qrcode(silent=True)
        self._login_poll_job = self.after(2500, self._login_poll_tick)

    def refresh_login_qrcode(self, silent: bool = False, *_args) -> None:
        def work():
            try:
                napcat = find_napcat_dir(self.cfg)
                if not napcat:
                    self.after(
                        0,
                        lambda: self.lbl_login_status.configure(
                            text="未找到 NapCat 目录，请确认 tools\\NapCat 存在", text_color="#e74c3c"
                        ),
                    )
                    return
                webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
                if not webui.ping():
                    self.after(
                        0,
                        lambda: self.lbl_login_status.configure(
                            text="NapCat 未就绪，请点击「启动 NapCat」", text_color="#e67e22"
                        ),
                    )
                    return
                st = webui.check_login_status()
                url = st.qrcode_url
                if not st.is_login:
                    try:
                        url = webui.get_qrcode_url() or url
                    except Exception:
                        pass
                png = qrcode_png_path(napcat)
                pil = load_qrcode_image(png, url)
                info = {}
                if st.is_login:
                    try:
                        info = webui.get_login_info()
                    except Exception:
                        pass
                self.after(0, lambda: self._apply_login_ui(st, pil, info, silent))
            except Exception as e:
                if not silent:
                    self.after(0, lambda: messagebox.showerror("登录页刷新失败", str(e)))
                self.after(
                    0,
                    lambda: self.lbl_login_status.configure(text=f"刷新失败: {e}", text_color="#e74c3c"),
                )

        threading.Thread(target=work, daemon=True).start()

    def _apply_login_ui(self, st, pil, info: dict, silent: bool) -> None:
        if pil is not None:
            self._qr_ctk_image = pil_to_ctk(pil)
            self.lbl_qr.configure(image=self._qr_ctk_image, text="")
        elif not st.is_login:
            self.lbl_qr.configure(image=None, text="等待二维码\n请先启动 NapCat")

        if st.is_login:
            nick = info.get("nick") or ""
            uin = info.get("uin") or ""
            self.lbl_login_status.configure(text="已登录 QQ", text_color="#2ecc71")
            self.lbl_login_uin.configure(text=f"{nick} ({uin})" if nick else str(uin))
            self.lbl_conn.configure(text=f"已连接 QQ {uin}", text_color="#2ecc71")
            if self._login_poll_job:
                self.after_cancel(self._login_poll_job)
                self._login_poll_job = None
            self.refresh_status()
        else:
            err = st.login_error or "请使用手机 QQ 扫描二维码"
            self.lbl_login_status.configure(text=err, text_color="#e67e22")
            self.lbl_login_uin.configure(text=st.qrcode_url[:80] + "…" if len(st.qrcode_url) > 80 else st.qrcode_url)
            self._fill_quick_login_buttons()

    def _fill_quick_login_buttons(self) -> None:
        for w in self.frame_quick_login.winfo_children():
            w.destroy()

        def work():
            try:
                napcat = find_napcat_dir(self.cfg)
                if not napcat:
                    return
                webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
                if not webui.ping():
                    return
                uins = webui.get_quick_login_list()
                self.after(0, lambda: self._render_quick_login(uins))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _render_quick_login(self, uins: list[str]) -> None:
        for w in self.frame_quick_login.winfo_children():
            w.destroy()
        if not uins:
            ctk.CTkLabel(self.frame_quick_login, text="（无历史账号，请扫码）", text_color="gray").pack(
                anchor="w", padx=4, pady=2
            )
            return
        for uin in uins[:30]:
            ctk.CTkButton(
                self.frame_quick_login,
                text=f"快速登录 {uin}",
                anchor="w",
                command=lambda u=uin: self.launch_napcat(u),
            ).pack(fill="x", padx=4, pady=2)

    def _after_login_setup(self) -> None:
        ok, msg = deploy_plugin(self.cfg)
        if ok:
            self.sync_settings()
            messagebox.showinfo("完成", msg + "\n\n即将进入实时监控。")
            self.tabs.set("实时监控")
            self.refresh_status()
            self.refresh_monitor()
        else:
            messagebox.showerror("部署失败", msg)

    def _build_status_tab(self) -> None:
        f = self.tab_status
        f.grid_columnconfigure(0, weight=1)

        box = ctk.CTkFrame(f)
        box.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        box.grid_columnconfigure(1, weight=1)

        rows = [
            ("NapCat 地址", "napcat_base"),
            ("当前 QQ", "self_uin"),
            ("缓存红包数", "record_count"),
            ("自动收红包", "auto_grab"),
            ("自动查详情", "auto_pull"),
        ]
        self.status_vars: dict[str, ctk.StringVar] = {}
        for i, (label, key) in enumerate(rows):
            ctk.CTkLabel(box, text=label + "：").grid(row=i, column=0, sticky="w", padx=12, pady=8)
            var = ctk.StringVar(value="-")
            self.status_vars[key] = var
            ctk.CTkLabel(box, textvariable=var, anchor="w").grid(row=i, column=1, sticky="w", padx=8, pady=8)

        btn_row = ctk.CTkFrame(f, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=8, pady=8)
        ctk.CTkButton(btn_row, text="同步设置到插件", command=self.sync_settings).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="一键部署插件", command=self.deploy_plugin_action).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="打开监控页", command=lambda: self.tabs.set("实时监控")).pack(
            side="left", padx=4
        )
        ctk.CTkButton(btn_row, text="清空运行日志", command=self.clear_status_log).pack(side="left", padx=4)

        self.log_status = ctk.CTkTextbox(f, height=220)
        self.log_status.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        f.grid_rowconfigure(2, weight=1)
        self._log_status("欢迎使用。请先确保 QQ 电脑版 + NapCat 已登录，并部署插件。")

    def _build_monitor_tab(self) -> None:
        f = self.tab_monitor
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=2)
        f.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=4)

        row0 = ctk.CTkFrame(top, fg_color="transparent")
        row0.pack(fill="x")
        row0.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row0, text="抢包群号（多群逗号分隔，留空=全部群）：").grid(
            row=0, column=0, sticky="w"
        )
        self.entry_monitor_group = ctk.CTkEntry(
            row0, placeholder_text="例：1108441408,666590652"
        )
        self.entry_monitor_group.grid(row=0, column=1, sticky="ew", padx=8)
        self.entry_monitor_group.insert(0, self.cfg.watch_groups)

        row1 = ctk.CTkFrame(top, fg_color="transparent")
        row1.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(
            row1,
            text="应用多群抢包",
            fg_color="#27ae60",
            hover_color="#1e8449",
            command=self.apply_multi_group_watch,
        ).pack(side="left", padx=4)
        ctk.CTkButton(row1, text="立即刷新", command=self.refresh_monitor).pack(side="left", padx=4)
        ctk.CTkButton(
            row1,
            text="一键清空",
            fg_color="#c0392b",
            hover_color="#a93226",
            command=self.clear_monitor_records,
        ).pack(side="left", padx=4)
        ctk.CTkButton(row1, text="加载群成员", command=self.load_group_members).pack(side="left", padx=4)
        self.lbl_poll = ctk.CTkLabel(row1, text="", text_color="gray")
        self.lbl_poll.pack(side="left", padx=12)
        self.lbl_watch_hint = ctk.CTkLabel(
            row1,
            text="改群号后点「应用多群抢包」（立即刷新也会自动同步）",
            text_color="#e67e22",
        )
        self.lbl_watch_hint.pack(side="left", padx=8)

        paned = ctk.CTkFrame(f)
        paned.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        paned.grid_columnconfigure(0, weight=2, minsize=280)
        paned.grid_columnconfigure(1, weight=3, minsize=320)
        paned.grid_rowconfigure(0, weight=1)

        # 红包列表
        left = ctk.CTkFrame(paned)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)
        hdr_left = ctk.CTkFrame(left, fg_color="transparent")
        hdr_left.grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        hdr_left.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr_left, text="最近红包").grid(row=0, column=0, sticky="w")
        self.lbl_selected_packet = ctk.CTkLabel(
            hdr_left, text="（点击一行查看详情）", text_color="gray", anchor="e"
        )
        self.lbl_selected_packet.grid(row=0, column=1, sticky="e")

        tree_frame = tk.Frame(left, highlightthickness=1, highlightbackground="#CBD5E1")
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        cols = ("time", "group", "sender", "bill")
        self.tree_packets = ttk.Treeview(
            tree_frame, columns=cols, show="headings", height=12, selectmode="browse"
        )
        for c, w, t in [
            ("time", 130, "时间"),
            ("group", 90, "群号"),
            ("sender", 100, "发送者"),
            ("bill", 120, "单号"),
        ]:
            self.tree_packets.heading(c, text=t)
            self.tree_packets.column(c, width=w)
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_packets.yview)
        self.tree_packets.configure(yscrollcommand=sb.set)
        self.tree_packets.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree_packets.bind("<<TreeviewSelect>>", self._on_packet_select)
        self.tree_packets.bind("<ButtonRelease-1>", self._on_packet_click, add="+")

        # 领取详情
        right = ctk.CTkFrame(paned)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)
        hdr = ctk.CTkFrame(right, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text="领取详情").grid(row=0, column=0, sticky="w")
        self.lbl_claims_summary = ctk.CTkLabel(hdr, text="", text_color="gray", anchor="w")
        self.lbl_claims_summary.grid(row=0, column=1, sticky="ew", padx=8)
        ctk.CTkButton(hdr, text="刷新详情", width=90, command=self.refresh_selected_detail).grid(
            row=0, column=2, sticky="e"
        )
        # 长说明单独一行并换行，避免把左侧「最近红包」挤没
        self.lbl_claims_note = ctk.CTkLabel(
            right,
            text="",
            text_color="#64748B",
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.lbl_claims_note.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))

        detail_frame = tk.Frame(right)
        detail_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        dcols = ("uin", "name", "amount", "time")
        self.tree_claims = ttk.Treeview(detail_frame, columns=dcols, show="headings", height=12)
        for c, w, t in [
            ("uin", 100, "QQ"),
            ("name", 100, "昵称"),
            ("amount", 70, "金额(元)"),
            ("time", 150, "领取时间"),
        ]:
            self.tree_claims.heading(c, text=t)
            self.tree_claims.column(c, width=w)
        sb2 = ttk.Scrollbar(detail_frame, orient="vertical", command=self.tree_claims.yview)
        self.tree_claims.configure(yscrollcommand=sb2.set)
        self.tree_claims.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")

        self._packet_cache: dict[str, dict] = {}

        # 群成员核查
        members_box = ctk.CTkFrame(f)
        members_box.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        members_box.grid_columnconfigure(0, weight=1)
        members_box.grid_rowconfigure(1, weight=1)

        mhdr = ctk.CTkFrame(members_box, fg_color="transparent")
        mhdr.grid(row=0, column=0, sticky="ew", padx=8, pady=4)
        ctk.CTkLabel(mhdr, text="群成员名单（核查群号是否正确）").pack(side="left")
        ctk.CTkLabel(mhdr, text="切换群：").pack(side="left", padx=(16, 4))
        groups0 = self._parse_watch_groups() or ["（先填写上方抢包群号）"]
        self.combo_members_group = ctk.CTkComboBox(
            mhdr,
            values=groups0,
            width=160,
            command=lambda _v: self.load_group_members(),
        )
        self.combo_members_group.pack(side="left", padx=4)
        if groups0 and not groups0[0].startswith("（"):
            self.combo_members_group.set(groups0[0])
        ctk.CTkButton(mhdr, text="加载此群成员", width=110, command=self.load_group_members).pack(
            side="left", padx=6
        )
        self.lbl_members_info = ctk.CTkLabel(mhdr, text="选择群后点「加载此群成员」", text_color="gray")
        self.lbl_members_info.pack(side="left", padx=12)

        members_frame = tk.Frame(members_box)
        members_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        mcols = ("uin", "name", "card", "role")
        self.tree_members = ttk.Treeview(members_frame, columns=mcols, show="headings", height=8)
        for c, w, t in [
            ("uin", 110, "QQ"),
            ("name", 120, "昵称"),
            ("card", 120, "群名片"),
            ("role", 70, "身份"),
        ]:
            self.tree_members.heading(c, text=t)
            self.tree_members.column(c, width=w)
        sb3 = ttk.Scrollbar(members_frame, orient="vertical", command=self.tree_members.yview)
        self.tree_members.configure(yscrollcommand=sb3.set)
        self.tree_members.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")

    def _build_query_tab(self) -> None:
        f = self.tab_query
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)

        form = ctk.CTkFrame(f)
        form.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="群号").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.entry_q_group = ctk.CTkEntry(form, placeholder_text="必填")
        self.entry_q_group.grid(row=0, column=1, padx=8, pady=6, sticky="ew")

        now = datetime.now()
        start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        end = now.strftime("%Y-%m-%d %H:%M:%S")

        ctk.CTkLabel(form, text="开始时间").grid(row=1, column=0, padx=8, pady=6, sticky="w")
        self.entry_q_start = ctk.CTkEntry(form)
        self.entry_q_start.insert(0, start)
        self.entry_q_start.grid(row=1, column=1, padx=8, pady=6, sticky="ew")

        ctk.CTkLabel(form, text="结束时间").grid(row=2, column=0, padx=8, pady=6, sticky="w")
        self.entry_q_end = ctk.CTkEntry(form)
        self.entry_q_end.insert(0, end)
        self.entry_q_end.grid(row=2, column=1, padx=8, pady=6, sticky="ew")

        self.chk_refresh = ctk.CTkCheckBox(form, text="查询时向 QQ 服务器重新拉取详情（可能较慢）")
        # 默认不勾：先用本地缓存，避免 pullDetail 穷举导致「查询卡住/失败」
        self.chk_refresh.deselect()
        self.chk_refresh.grid(row=3, column=1, padx=8, pady=6, sticky="w")

        ctk.CTkButton(form, text="查询", command=self.run_query).grid(row=0, column=2, rowspan=4, padx=12)
        ctk.CTkButton(form, text="导出表格", command=self.export_query_csv).grid(
            row=4, column=2, padx=12, pady=6, sticky="n"
        )
        ctk.CTkLabel(form, text="查询完成后可导出 CSV（Excel 可直接打开）", text_color="gray").grid(
            row=4, column=0, columnspan=2, padx=8, pady=4, sticky="w"
        )

        self.txt_query_result = ctk.CTkTextbox(f)
        self.txt_query_result.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)

    def _build_settings_tab(self) -> None:
        f = self.tab_settings
        f.grid_columnconfigure(1, weight=1)

        fields: list[tuple[str, str, str]] = [
            ("NapCat WebUI 地址", "napcat_base", "http://127.0.0.1:6099"),
            ("插件目录 ID", "plugin_id", "napcat-plugin-cleaner（勿改）"),
            ("监听群号（逗号分隔）", "watch_groups", ""),
            ("NapCat plugins 目录", "napcat_plugins_dir", "留空则自动猜测"),
        ]
        self.setting_entries: dict[str, ctk.CTkEntry] = {}
        for i, (label, key, ph) in enumerate(fields):
            ctk.CTkLabel(f, text=label).grid(row=i, column=0, padx=12, pady=8, sticky="w")
            e = ctk.CTkEntry(f, placeholder_text=ph)
            e.grid(row=i, column=1, padx=8, pady=8, sticky="ew")
            val = getattr(self.cfg, key, "")
            if val:
                e.insert(0, str(val))
            self.setting_entries[key] = e
            if key == "napcat_plugins_dir":
                ctk.CTkButton(f, text="浏览…", width=70, command=self._browse_plugins_dir).grid(
                    row=i, column=2, padx=4
                )

        row = len(fields)
        self.sw_auto_grab = ctk.CTkSwitch(f, text="自动收红包")
        self.sw_auto_grab.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.auto_grab:
            self.sw_auto_grab.select()

        self.sw_grab_self = ctk.CTkSwitch(f, text="允许领取自己发的包（拼手气）")
        self.sw_grab_self.grid(row=row + 1, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.grab_self:
            self.sw_grab_self.select()

        self.sw_auto_detail = ctk.CTkSwitch(f, text="自动查领取详情")
        self.sw_auto_detail.grid(row=row + 2, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.auto_pull_detail:
            self.sw_auto_detail.select()

        self.sw_password = ctk.CTkSwitch(f, text="自动发口令（口令红包）")
        self.sw_password.grid(row=row + 3, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.handle_password:
            self.sw_password.select()

        ctk.CTkLabel(f, text="领取延迟(ms)").grid(row=row + 4, column=0, padx=12, pady=8, sticky="w")
        delay_frame = ctk.CTkFrame(f, fg_color="transparent")
        delay_frame.grid(row=row + 4, column=1, sticky="w", padx=8)
        self.entry_delay_min = ctk.CTkEntry(delay_frame, width=70)
        self.entry_delay_min.insert(0, str(self.cfg.delay_min_ms))
        self.entry_delay_min.pack(side="left")
        ctk.CTkLabel(delay_frame, text=" ~ ").pack(side="left")
        self.entry_delay_max = ctk.CTkEntry(delay_frame, width=70)
        self.entry_delay_max.insert(0, str(self.cfg.delay_max_ms))
        self.entry_delay_max.pack(side="left")

        ctk.CTkButton(f, text="保存设置", command=self.save_settings).grid(
            row=row + 5, column=1, sticky="w", padx=8, pady=16
        )

    def _build_help_tab(self) -> None:
        text = ctk.CTkTextbox(self.tab_help)
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert(
            "1.0",
            """【客户使用 — 开箱步骤】

1. 电脑先安装 QQ 电脑版（NT 版）
   https://im.qq.com/

2. 双击「agent.exe」

3. 打开「QQ 扫码登录」页 → 点「启动 NapCat」→ 用手机 QQ 扫二维码

4. 登录成功后点「登录成功后：部署插件并进入监控」

5. 在「实时监控」填写抢包群号（多个群用英文逗号分隔），点「应用多群抢包」
   例：1108441408,666590652
   或「设置」里同样填写后点「同步设置到插件」

6. 「实时监控」查看红包；「历史查询」可导出 CSV 表格

【说明】
• 安装包需包含 tools\\NapCat 目录（与 exe 同级）
• 无需打开黑色命令行窗口，均在软件内操作
• 发红包请在 QQ 客户端手动发送
• 同一账号多群：只开一个软件窗口，群号逗号分隔即可同时抢
• 不要多开两个软件窗口分别填不同群（无效，仍共用一个插件）

【三个功能】
• 收红包：插件自动 grabRedBag（所有监听群都会抢）
• 查红包：展示领取人、时间、金额，可导出表格
• 监控群：填写群号后自动刷新

【注意】
• 仅 QQ 钱包红包有效
• 启动 NapCat 不会关闭您已打开的 QQ 电脑版；监控账号需在软件内单独登录
• 本软件一次只能监控一个 QQ 账号（但该账号可同时监听多个群）
• 自动化有封号风险，请合规使用
""",
        )
        text.configure(state="disabled")

    # ---------- actions ----------

    def _log_status(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_status.insert("end", f"[{ts}] {msg}\n")
        self.log_status.see("end")

    def _apply_cfg_from_settings_ui(self) -> None:
        self.cfg.napcat_base = self.setting_entries["napcat_base"].get().strip()
        self.cfg.plugin_id = self.setting_entries["plugin_id"].get().strip() or self.cfg.plugin_id
        self.cfg.watch_groups = self.setting_entries["watch_groups"].get().strip()
        self.cfg.napcat_plugins_dir = self.setting_entries["napcat_plugins_dir"].get().strip()
        self.cfg.auto_grab = bool(self.sw_auto_grab.get())
        self.cfg.grab_self = bool(self.sw_grab_self.get())
        self.cfg.auto_pull_detail = bool(self.sw_auto_detail.get())
        self.cfg.handle_password = bool(self.sw_password.get())
        try:
            self.cfg.delay_min_ms = int(self.entry_delay_min.get())
            self.cfg.delay_max_ms = int(self.entry_delay_max.get())
        except ValueError:
            pass
        save_config(self.cfg)
        self.client = PluginClient(self.cfg)

    def save_settings(self) -> None:
        self._apply_cfg_from_settings_ui()
        self.cfg.first_run_done = True
        save_config(self.cfg)
        messagebox.showinfo("保存", "设置已保存。")
        self.refresh_status()

    def sync_settings(self) -> None:
        self._apply_cfg_from_settings_ui()

        def work():
            try:
                self.client.sync_plugin_config()
                self.after(0, lambda: self._log_status("已同步设置到 NapCat 插件"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("同步失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def deploy_plugin_action(self) -> None:
        self._apply_cfg_from_settings_ui()
        dest = None
        if self.cfg.napcat_plugins_dir:
            from pathlib import Path

            # 传 plugins 根目录；deploy_plugin 会强制使用白名单 ID
            dest = Path(self.cfg.napcat_plugins_dir)
        ok, msg = deploy_plugin(self.cfg, dest)
        if ok:
            messagebox.showinfo("部署成功", msg)
            self._log_status("插件部署成功")
        else:
            messagebox.showerror("部署失败", msg)

    def refresh_status(self) -> None:
        self._apply_cfg_from_settings_ui()

        def work():
            st = self.client.get_status()
            self.after(0, lambda: self._update_status_ui(st))

        threading.Thread(target=work, daemon=True).start()

    def _update_status_ui(self, st) -> None:
        if st.connected:
            was_ready = self._napcat_ready
            self._napcat_ready = True
            self.lbl_conn.configure(text=f"已连接 QQ {st.self_uin or '?'}", text_color="#2ecc71")
            self.status_vars["napcat_base"].set(self.cfg.napcat_base)
            self.status_vars["self_uin"].set(st.self_uin or "-")
            self.status_vars["record_count"].set(str(st.record_count))
            self.status_vars["auto_grab"].set("是" if st.auto_grab else "否")
            self.status_vars["auto_pull"].set("是" if st.auto_pull_detail else "否")
            self._log_status(f"连接正常，缓存 {st.record_count} 条红包")
            if self._login_poll_job:
                self.after_cancel(self._login_poll_job)
                self._login_poll_job = None
            if not was_ready or not self._polling:
                self._start_poll()
        else:
            self._napcat_ready = False
            self.lbl_conn.configure(text="未连接 NapCat/插件", text_color="#e74c3c")
            err = st.raw.get("error", "")
            if _is_startup_noise(err):
                err = "请打开「QQ 扫码登录」页，点「启动 NapCat」后扫码（启动约需 15~60 秒）"
            self._log_status(f"连接失败: {err}")
            try:
                self.tabs.set("QQ 扫码登录")
            except Exception:
                pass
            self._schedule_login_poll()
            self.refresh_login_qrcode(silent=True)

    def _parse_watch_groups(self, raw: str | None = None) -> list[str]:
        text = raw if raw is not None else self.entry_monitor_group.get()
        text = (text or "").replace("，", ",")
        return [x.strip() for x in text.split(",") if x.strip()]

    def _refresh_members_group_combo(self, prefer: str | None = None) -> None:
        if not hasattr(self, "combo_members_group"):
            return
        groups = self._parse_watch_groups()
        if not groups:
            self.combo_members_group.configure(values=["（先填写上方抢包群号）"])
            self.combo_members_group.set("（先填写上方抢包群号）")
            return
        cur = prefer or self.combo_members_group.get()
        self.combo_members_group.configure(values=groups)
        if cur in groups:
            self.combo_members_group.set(cur)
        else:
            self.combo_members_group.set(groups[0])

    def apply_multi_group_watch(self) -> None:
        """把监控页群号同步到插件，多群同时抢包。"""
        groups = self._parse_watch_groups()
        self.cfg.watch_groups = ",".join(groups)
        save_config(self.cfg)
        if "watch_groups" in getattr(self, "setting_entries", {}):
            e = self.setting_entries["watch_groups"]
            e.delete(0, "end")
            e.insert(0, self.cfg.watch_groups)
        self.client = PluginClient(self.cfg)
        self._refresh_members_group_combo()

        def work():
            try:
                self.client.sync_plugin_config()
                tip = (
                    f"已同步 {len(groups)} 个群同时抢包：{', '.join(groups)}"
                    if groups
                    else "已同步：监听全部群并抢包（未限制群号）"
                )
                self.after(0, lambda: self._log_status(tip))
                self.after(0, lambda: messagebox.showinfo("多群抢包已启用", tip))
                self.after(0, self.refresh_monitor)
                self.after(0, self.refresh_status)
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("同步失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _log_wait_throttled(self, text: str) -> None:
        import time

        now = time.time()
        if now - self._last_wait_log_at < 8:
            return
        self._last_wait_log_at = now
        self._log_status(text)

    def refresh_monitor(self) -> None:
        if not self._napcat_ready:
            self._log_wait_throttled("等待 NapCat / QQ 登录就绪后再刷新监控…")
            return

        groups = self._parse_watch_groups()
        # 输入框改了群号但未点「应用」时，刷新也顺带同步，避免漏抢
        self.cfg.watch_groups = ",".join(groups)
        save_config(self.cfg)
        self._refresh_members_group_combo()

        def work():
            try:
                try:
                    self.client.sync_plugin_config()
                except Exception as sync_e:
                    self.after(0, lambda: self._log_status(f"同步监听群失败: {sync_e}"))
                if len(groups) <= 1:
                    items = self.client.list_records(groups[0] if groups else None)
                else:
                    all_items = self.client.list_records(None)
                    allow = set(groups)
                    items = [it for it in all_items if str(it.get("group_id") or "") in allow]
                self.after(0, lambda: self._fill_packet_tree(items))
            except Exception as e:
                if _is_startup_noise(e):
                    self._napcat_ready = False
                    self.after(
                        0,
                        lambda: self._log_wait_throttled(
                            "NapCat/插件尚未就绪，请稍候或到「QQ 扫码登录」页确认已登录"
                        ),
                    )
                    self.after(0, self.refresh_status)
                else:
                    self.after(0, lambda: self._log_status(f"监控刷新失败: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def clear_monitor_records(self) -> None:
        if not messagebox.askyesno(
            "确认清空",
            "确定清空所有已缓存的红包监控记录？\n"
            "（实时监控、历史查询中的数据都会清空，不可恢复）",
        ):
            return

        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

        def work():
            try:
                n = self.client.clear_records()
                self.after(0, lambda: self._after_clear_records(n))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("清空失败", str(e)))
            finally:
                self.after(0, self._start_poll)

        threading.Thread(target=work, daemon=True).start()

    def _after_clear_records(self, count: int) -> None:
        self._selected_bill = ""
        self._fill_packet_tree([])
        for iid in self.tree_claims.get_children():
            self.tree_claims.delete(iid)
        self.lbl_selected_packet.configure(text="（点击一行查看详情）", text_color="gray")
        if hasattr(self, "lbl_claims_summary"):
            self.lbl_claims_summary.configure(text="")
        if hasattr(self, "lbl_claims_note"):
            self.lbl_claims_note.configure(text="")
        self.txt_query_result.delete("1.0", "end")
        self._log_status(f"已清空 {count} 条红包监控记录")
        self.refresh_status()
        self.refresh_monitor()

    def clear_status_log(self) -> None:
        if not messagebox.askyesno("确认清空", "确定清空运行日志？"):
            return
        self.log_status.delete("1.0", "end")

    def _fill_packet_tree(self, items: list[dict]) -> None:
        # 优先保留用户手动点选的单号，避免轮询重建列表时把选中项拽回去
        prefer = self._selected_bill
        if not prefer:
            prev_sel = self.tree_packets.selection()
            prefer = prev_sel[0] if prev_sel else ""

        new_cache: dict[str, dict] = {}
        rows: list[tuple[str, tuple]] = []
        for it in items:
            bill = str(it.get("bill_no") or "")
            if not bill:
                continue
            new_cache[bill] = it
            bill_show = bill if len(bill) <= 18 else f"…{bill[-14:]}"
            rows.append(
                (
                    bill,
                    (
                        it.get("msg_time_text") or "",
                        it.get("group_id") or "",
                        it.get("sender_name") or it.get("sender_uin") or "",
                        bill_show,
                    ),
                )
            )

        old_iids = list(self.tree_packets.get_children())
        new_iids = [b for b, _ in rows]
        same_order = old_iids == new_iids

        self._filling_packets = True
        try:
            self._packet_cache = new_cache
            if same_order:
                for bill, values in rows:
                    try:
                        self.tree_packets.item(bill, values=values)
                    except Exception:
                        same_order = False
                        break
            if not same_order:
                for iid in old_iids:
                    self.tree_packets.delete(iid)
                for bill, values in rows:
                    self.tree_packets.insert("", "end", iid=bill, values=values)

            if prefer and prefer in self._packet_cache:
                self.tree_packets.selection_set(prefer)
                self.tree_packets.focus(prefer)
                self._update_selected_packet_label(prefer)
                # 同步详情（不依赖再次触发 select，避免重建时丢内容）
                pkt = self._packet_cache.get(prefer, {})
                self._fill_claims_tree(
                    pkt.get("claims") or [], pkt.get("summary"), pkt.get("claims_note") or ""
                )
            elif not prefer:
                self.lbl_selected_packet.configure(text="（点击一行查看详情）", text_color="gray")
        finally:
            self._filling_packets = False
        self.lbl_poll.configure(text=f"更新于 {datetime.now().strftime('%H:%M:%S')}")

    def _update_selected_packet_label(self, bill: str) -> None:
        pkt = self._packet_cache.get(bill, {})
        sender = pkt.get("sender_name") or pkt.get("sender_uin") or "-"
        summary = pkt.get("summary") or {}
        extra = ""
        if summary.get("recv_num") is not None and summary.get("total_num") is not None:
            extra = f" · 已领 {summary.get('recv_num')}/{summary.get('total_num')}"
        short = f"{bill[:16]}…" if len(bill) > 16 else bill
        self.lbl_selected_packet.configure(
            text=f"已选：{sender} · {short}{extra}",
            text_color="#2563EB",
        )

    def _on_packet_click(self, _evt=None) -> None:
        self._user_gen += 1
        self.after_idle(self._sync_packet_selection)

    def _sync_packet_selection(self) -> None:
        sel = self.tree_packets.selection()
        if sel:
            self._selected_bill = sel[0]
            self._update_selected_packet_label(sel[0])

    def _on_packet_select(self, _evt=None) -> None:
        if self._filling_packets:
            return
        sel = self.tree_packets.selection()
        if not sel:
            self.lbl_selected_packet.configure(text="（点击一行查看详情）", text_color="gray")
            return
        bill = sel[0]
        self._selected_bill = bill
        self._update_selected_packet_label(bill)
        pkt = self._packet_cache.get(bill, {})
        self._fill_claims_tree(pkt.get("claims") or [], pkt.get("summary"), pkt.get("claims_note") or "")

    def load_group_members(self) -> None:
        self._refresh_members_group_combo()
        groups = self._parse_watch_groups()
        if not groups:
            messagebox.showwarning("提示", "请先在上方填写抢包群号（可多个，逗号分隔）")
            return

        selected = ""
        if hasattr(self, "combo_members_group"):
            selected = (self.combo_members_group.get() or "").strip()
        if selected not in groups:
            selected = groups[0]
            self.combo_members_group.set(selected)

        self.lbl_members_info.configure(text=f"正在加载群 {selected}…", text_color="gray")

        def work():
            try:
                data = self.client.get_group_members(selected)
                self.after(0, lambda: self._fill_members_tree(data))
            except Exception as e:
                self.after(
                    0,
                    lambda: self.lbl_members_info.configure(
                        text=f"加载失败: {e}", text_color="#e74c3c"
                    ),
                )
                self.after(0, lambda: self._log_status(f"群成员加载失败: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _fill_members_tree(self, data: dict) -> None:
        for iid in self.tree_members.get_children():
            self.tree_members.delete(iid)
        members = data.get("members") or []
        for m in members:
            self.tree_members.insert(
                "",
                "end",
                values=(
                    m.get("uin") or "",
                    m.get("nickname") or "",
                    m.get("card") or "",
                    m.get("role_text") or m.get("role") or "",
                ),
            )
        gid = data.get("group_id") or ""
        gname = data.get("group_name") or ""
        count = data.get("member_count") or len(members)
        title = f"群 {gid}"
        if gname:
            title += f" · {gname}"
        title += f" · 共 {count} 人"
        warning = data.get("warning") or ""
        if warning:
            title += f" · {warning}"
            self.lbl_members_info.configure(text=title, text_color="#e67e22")
        else:
            self.lbl_members_info.configure(text=title, text_color="#2ecc71")
        self._log_status(f"已加载群成员 {gid}，共 {count} 人")

    def _fill_claims_tree(self, claims: list[dict], summary: dict | None = None, note: str = "") -> None:
        for iid in self.tree_claims.get_children():
            self.tree_claims.delete(iid)
        if summary:
            recv_n = summary.get("recv_num")
            total_n = summary.get("total_num")
            recv_a = summary.get("recv_amount")
            total_a = summary.get("total_amount")
            parts = []
            if recv_n is not None and total_n is not None:
                parts.append(f"已领 {recv_n}/{total_n} 份")
            if recv_a is not None and total_a is not None:
                parts.append(f"共 {recv_a}/{total_a} 元")
            lucky = summary.get("lucky_name") or summary.get("lucky_uin")
            if lucky and str(lucky) not in ("0", "None", "null"):
                parts.append(f"手气最佳 {lucky}")
            self.lbl_claims_summary.configure(text=" · ".join(parts) if parts else "")
        else:
            self.lbl_claims_summary.configure(text="")

        # 长说明放独立换行标签，绝不塞进 summary（否则会挤掉左侧列表）
        note_text = (note or "").strip()
        if hasattr(self, "lbl_claims_note"):
            self.lbl_claims_note.configure(text=note_text)

        for c in claims:
            uin = str(c.get("uin") or "").strip()
            name = str(c.get("name") or "").strip()
            if uin == "0":
                uin = ""
            if not uin and not name:
                continue
            amt = c.get("amount")
            amt_text = "-" if amt in (None, "", 0, 0.0) else amt
            if "另有" in name and ("未同步" in name or "无法显示" in name):
                amt_text = "-"
            self.tree_claims.insert(
                "",
                "end",
                values=(
                    uin,
                    name,
                    amt_text,
                    c.get("time_text") or c.get("time") or "",
                ),
            )

    def refresh_selected_detail(self) -> None:
        sel = self.tree_packets.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个红包")
            return
        bill = sel[0]

        def work():
            try:
                pkt = self.client.pull_detail(bill)
                self._packet_cache[bill] = pkt
                self.after(
                    0,
                    lambda b=bill, p=pkt: (
                        self._update_selected_packet_label(b),
                        self._fill_claims_tree(p.get("claims") or [], p.get("summary"), p.get("claims_note") or ""),
                    ),
                )
                self.after(0, lambda: self._log_status(f"已刷新详情 {bill}"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("失败", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def run_query(self) -> None:
        gid = self.entry_q_group.get().strip()
        if not gid:
            messagebox.showwarning("提示", "请填写群号")
            return
        start = self.entry_q_start.get().strip()
        end = self.entry_q_end.get().strip()
        refresh = bool(self.chk_refresh.get())

        self.txt_query_result.delete("1.0", "end")
        self.txt_query_result.insert("end", "查询中…\n")

        def work():
            try:
                data = self.client.query(gid, start, end, refresh=refresh)
                lines = [
                    f"群 {data.get('group_id')}  {data.get('start_text')} ~ {data.get('end_text')}",
                    f"共 {data.get('count', 0)} 个红包\n",
                ]
                for pkt in data.get("packets") or []:
                    lines.append(
                        f"【红包】{pkt.get('bill_no')}  发送者 {pkt.get('sender_name')}({pkt.get('sender_uin')})"
                    )
                    lines.append(f"  时间 {pkt.get('msg_time_text')}  祝福语 {pkt.get('wishing')}")
                    claims = pkt.get("claims") or []
                    summary = pkt.get("summary")
                    if summary:
                        lines.append(
                            f"  汇总 已领 {summary.get('recv_num')}/{summary.get('total_num')} 份，"
                            f"共 {summary.get('recv_amount')}/{summary.get('total_amount')} 元"
                        )
                    if not claims:
                        lines.append("  （暂无领取明细）")
                    for c in claims:
                        amt = c.get("amount")
                        amt_s = "-" if amt in (None, "", 0, 0.0) else f"{amt}元"
                        lines.append(
                            f"  · {c.get('name') or '-'}({c.get('uin')})  "
                            f"{amt_s}  {c.get('time_text')}"
                        )
                    lines.append("")
                text = "\n".join(lines)
                self.after(0, lambda d=data, t=text: self._set_query_result(t, d))
            except ApiError as e:
                self.after(0, lambda: self._set_query_result(f"查询失败: {e}"))
            except Exception as e:
                self.after(0, lambda: self._set_query_result(f"错误: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _set_query_result(self, text: str, data: dict | None = None) -> None:
        if data is not None:
            self._last_query_data = data
        self.txt_query_result.delete("1.0", "end")
        self.txt_query_result.insert("end", text)

    def export_query_csv(self) -> None:
        if not self._last_query_data:
            messagebox.showwarning("提示", "请先执行一次历史查询，再导出表格")
            return
        packets = self._last_query_data.get("packets") or []
        if not packets:
            messagebox.showinfo("提示", "当前查询结果为空，没有可导出的数据")
            return

        gid = self._last_query_data.get("group_id") or self.entry_q_group.get().strip() or "query"
        default_name = f"红包查询_{gid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="导出查询结果为表格",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 表格", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            count = write_query_csv(self._last_query_data, path)
            messagebox.showinfo("导出成功", f"已导出 {count} 行到：\n{path}")
            self._log_status(f"已导出查询表格 {count} 行")
        except OSError as e:
            messagebox.showerror("导出失败", f"无法写入文件：{e}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _browse_plugins_dir(self) -> None:
        d = filedialog.askdirectory(title="选择 NapCat 的 plugins 目录")
        if d:
            self.setting_entries["napcat_plugins_dir"].delete(0, "end")
            self.setting_entries["napcat_plugins_dir"].insert(0, d)

    def _show_welcome(self) -> None:
        messagebox.showinfo(
            "首次使用",
            "请先安装 QQ 电脑版 + NapCat 并登录。\n\n"
            "然后在「设置」中配置地址，点击「一键部署插件」。\n"
            "详细步骤见「使用说明」页。",
        )

    def _start_poll(self) -> None:
        self._polling = True
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if not self._polling:
            return
        self.refresh_monitor()
        sec = max(1.0, float(self.cfg.poll_interval_sec))
        self._poll_job = self.after(int(sec * 1000), self._schedule_poll)

    def _build_backend_tab(self) -> None:
        """总后台对接（执行器功能）：独立模块挂载，见 backend_tab.BackendTab。"""
        self.backend_tab = BackendTab(
            self.tab_backend,
            cfg=self.cfg,
            client=self.client,
            save_config=save_config,
            app_version=APP_VERSION,
        )
        self.backend_tab.pack(fill="both", expand=True)

    def _on_close(self) -> None:
        self._polling = False
        if self._login_poll_job:
            try:
                self.after_cancel(self._login_poll_job)
            except Exception:
                pass
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        # 停掉总后台对接线程，避免关窗后残留 daemon 线程刷事件
        try:
            if getattr(self, "backend_tab", None):
                self.backend_tab.shutdown()
        except Exception:
            pass
        self.destroy()
