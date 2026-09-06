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
from .play_tab import PlayTab
from .approve_tab import ApprovalTab
from .napcat_paths import find_napcat_dir
from .napcat_webui import NapCatWebUI
from .plugin_deploy import deploy_plugin
from .qr_image import pil_to_ctk, qrcode_image
from .query_export import write_query_csv

from integration.backend_client import BackendError, ExecutorBanned, HbjfClient, OperatorDisabled
from integration.member_sync import group_create_time_str, run_member_sync

APP_VERSION = "2026.09.06-10"

HELP_TEXT = """【开箱步骤】
1. 双击「agent.exe」，软件自动启动 QQ 环境（约半分钟）
2. 用手机 QQ 扫描二维码登录
3. 登录成功后自动进入主界面

【群管理】
• 「群管理」页：点「＋ 添加群」输入群号；选中群后可开启/关闭监控、同步会员
• 「会员」页：选择群 → 「全部同步」，把群成员登记为会员并拉取积分

【游戏玩法】
• 「游戏玩法」页：勾选要参与的群 → 选中玩法 → 「启用玩法」
• 群成员发言自动按玩法回复并计分；「结算并上报」把积分报给总后台
• 红包玩法：总后台选择含 handle_redpacket 的玩法文件（如 rule_redpacket）即自动启用——管理员发红包、成员领取自动计分，领完自动@全体结算

【总后台对接】
• 填总后台地址 / 对接密钥 / 执行器 token → 「启动对接」（由管理员配置）

【退出登录 / 换 QQ】
• 主界面右上角「退出登录」：立即停止本机 QQ 与 NapCat 服务（QQ 退出登录、服务进程结束）
• 需要再次登录时，回登录页点「启动 QQ 环境并扫码登录」重新拉起，扫码即可换号

【注意】
• 仅 QQ 钱包红包有效
• 本软件不会关闭您已打开的 QQ；监控账号需在软件内单独扫码登录
• 同一账号可同时管理多个群，无需多开窗口
• 自动化有封号风险，请合规使用
"""


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
        self._main_built = False
        self._login_setup_done = False
        self._env_started = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 打开即自动准备环境（检测 → 未就绪自动拉起），全程只显示扫码页
        self.after(200, self._auto_env_start)
        self._schedule_login_poll()

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
        if hasattr(self, "tree_groups"):
            self.tree_groups.configure(style="Monitor.Treeview")
        if hasattr(self, "tree_members_tab"):
            self.tree_members_tab.configure(style="Detail.Treeview")

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- 登录视图（未登录时只显示这一页）----
        self.view_login = ctk.CTkFrame(self)
        self.view_login.grid_columnconfigure(0, weight=1)
        self.view_login.grid_rowconfigure(1, weight=1)
        self._build_login_view()

        # ---- 主视图（登录成功后懒构建）----
        self.view_main = ctk.CTkFrame(self)
        self.view_main.grid_columnconfigure(0, weight=1)
        self.view_main.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.view_main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text="agent 执行器", font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.lbl_conn = ctk.CTkLabel(header, text="连接: 检测中…", text_color="gray")
        self.lbl_conn.grid(row=0, column=1, sticky="e", padx=8)
        ctk.CTkButton(header, text="刷新", width=100, command=self.refresh_status).grid(
            row=0, column=2, sticky="e"
        )
        ctk.CTkButton(
            header, text="退出登录", width=100, fg_color="#c0392b", hover_color="#96281b",
            command=self.logout_qq,
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))

        self.tabs = ctk.CTkTabview(self.view_main)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=12)
        self.tab_groups = self.tabs.add("群管理")
        self.tab_members = self.tabs.add("会员")
        self.tab_play = self.tabs.add("游戏玩法")
        self.tab_approve = self.tabs.add("积分审批")
        self.tab_monitor = self.tabs.add("实时监控")
        self.tab_backend = self.tabs.add("总后台对接")
        self.tab_more = self.tabs.add("更多")

        self._show_login_view()

    # ---------- 视图切换 ----------

    def _show_login_view(self) -> None:
        """切到扫码页（未登录/掉线时）。"""
        self.view_main.grid_remove()
        self.view_login.grid(row=0, column=0, sticky="nsew")
        self._login_setup_done = False

    def _show_main_view(self) -> None:
        """切到主界面（首次会构建全部页面）。"""
        self._ensure_main_view()
        self.view_login.grid_remove()
        self.view_main.grid(row=0, column=0, sticky="nsew")

    def _ensure_main_view(self) -> None:
        """登录成功后懒构建主界面各页面（只执行一次）。"""
        if self._main_built:
            return
        self._main_built = True
        self._build_groups_tab()
        self._build_members_tab()
        self._build_play_tab()
        self._build_approve_tab()
        self._build_monitor_tab()
        self._build_backend_tab()
        self._build_more_tab()
        self._setup_tree_styles()
        self.tabs.set("群管理")

    def _build_login_view(self) -> None:
        f = self.view_login
        f.grid_columnconfigure(0, weight=1)
        f.grid_columnconfigure(1, weight=1)
        f.grid_rowconfigure(1, weight=1)

        tip = ctk.CTkLabel(
            f,
            text="首次使用：软件会自动准备环境，就绪后用手机 QQ 扫码，登录成功后自动进入主界面",
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
        ctk.CTkButton(
            left, text="刷新二维码", command=lambda: self.refresh_login_qrcode(force_refresh=True)
        ).pack(pady=(0, 12))

        right = ctk.CTkFrame(f)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=8)
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text="登录状态", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(12, 8)
        )
        self.lbl_login_status = ctk.CTkLabel(
            right, text="环境启动中…", text_color="#e67e22", wraplength=360, justify="left"
        )
        self.lbl_login_status.grid(row=1, column=0, sticky="w", padx=16, pady=4)
        self.lbl_login_uin = ctk.CTkLabel(right, text="", wraplength=360, justify="left")
        self.lbl_login_uin.grid(row=2, column=0, sticky="w", padx=16, pady=4)

        self.btn_start_env = ctk.CTkButton(
            right,
            text="启动 QQ 环境并扫码登录",
            height=36,
            state="disabled",
            command=self._start_env_and_login,
        )
        self.btn_start_env.grid(row=3, column=0, sticky="ew", padx=16, pady=(20, 4))
        ctk.CTkLabel(
            right,
            text="登录成功后自动进入主界面（无需其他操作）",
            text_color="gray",
        ).grid(row=4, column=0, sticky="w", padx=16, pady=4)
        right.grid_rowconfigure(5, weight=1)

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

    def _auto_env_start(self) -> None:
        """打开软件即自动准备环境：检测服务 → 未就绪自动拉起 → 就绪后提示扫码。
        全程只更新扫码页的状态文字，不弹技术名词。"""
        if self._env_started:
            return
        self._env_started = True
        self.lbl_login_status.configure(text="环境启动中…", text_color="#e67e22")

        def work():
            import time

            napcat = find_napcat_dir(self.cfg)
            if not napcat:
                self.after(0, lambda: self.lbl_login_status.configure(
                    text="未找到服务组件，请确认安装包完整（缺少 tools 目录）", text_color="#e74c3c"))
                self.after(0, lambda: self.btn_start_env.configure(state="normal"))
                return
            webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
            for _ in range(4):
                if webui.ping():
                    break
                time.sleep(1.5)
            if webui.ping():
                self.after(0, lambda: self.lbl_login_status.configure(
                    text="已就绪，请用手机 QQ 扫码", text_color="#e67e22"))
                self.after(0, lambda: self.refresh_login_qrcode(silent=True))
                return
            # 未就绪 → 自动拉起（复用 launch_napcat 的拉起逻辑）
            self.after(0, self.launch_napcat)
            for _ in range(60):  # 最多约 90 秒
                time.sleep(1.5)
                if webui.ping():
                    self.after(0, lambda: self.lbl_login_status.configure(
                        text="已就绪，请用手机 QQ 扫码", text_color="#e67e22"))
                    self.after(0, lambda: self.refresh_login_qrcode(silent=True))
                    return
            self.after(0, lambda: self.lbl_login_status.configure(
                text="环境启动超时，请点下方按钮重试", text_color="#e74c3c"))
            self.after(0, lambda: self.btn_start_env.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    def _start_env_and_login(self) -> None:
        """登录页「启动 QQ 环境并扫码登录」：环境停止（如刚退出登录）后手动拉起。"""
        self.launch_napcat()

    def logout_qq(self) -> None:
        """「退出登录」：停止本机 QQ 与 NapCat（QQ 退出登录、服务进程全部结束）。

        关窗口并不会退出 QQ——QQ/NapCat 进程保持运行，所以重开 agent 会检测到
        已登录而直接进主界面，永远到不了扫码页、无法换号。本按钮就是为此提供：
        彻底杀掉 QQ 与 NapCat → 停在登录页，等用户点「启动 QQ 环境并扫码登录」
        再干净地重新拉起（不自动重启，避免退出后又卡在拉起的坏状态上）。
        """
        if not messagebox.askyesno(
            "退出登录",
            "将停止本机 QQ 与 NapCat 服务：QQ 退出登录，红包监控/玩法全部停止。\n\n"
            "需要再次使用时，回登录页点「启动 QQ 环境并扫码登录」重新拉起（约 15~60 秒后扫码）。\n\n"
            "确认退出当前 QQ 吗？",
            icon="warning",
        ):
            return
        # 停监控轮询与登录轮询，避免 QQ 消失期间 404/503 噪音刷屏、状态被覆盖
        self._polling = False
        self._napcat_ready = False
        for attr in ("_poll_job", "_login_poll_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._show_login_view()
        self.lbl_login_status.configure(text="正在退出当前 QQ…", text_color="#e67e22")

        def kill_all():
            # 杀 NapCat 引导进程树与 QQ 树，并等 QQ 进程真正消失（最长约 15 秒）
            import time

            try:
                for img in ("NapCatWinBootMain.exe", "QQ.exe"):
                    subprocess.run(["taskkill", "/F", "/IM", img, "/T"],
                                   capture_output=True, timeout=20)
            except Exception:
                pass
            for _ in range(30):
                try:
                    out = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq QQ.exe"],
                        capture_output=True, text=True, timeout=10).stdout
                    if "QQ.exe" not in out:
                        break
                except Exception:
                    pass
                time.sleep(0.5)
            self.after(0, self._after_logout_done)

        threading.Thread(target=kill_all, daemon=True).start()

    def _after_logout_done(self) -> None:
        """退出完成收尾：停在登录页，等待用户决定何时重新拉起环境。"""
        self.lbl_login_status.configure(
            text="已退出登录：QQ 与 NapCat 已停止", text_color="#2ecc71")
        self.lbl_login_uin.configure(
            text="需要重新使用时，点下方「启动 QQ 环境并扫码登录」，再扫码即可（约 15~60 秒后出二维码）")
        self.btn_start_env.configure(state="normal")

    def launch_napcat(self) -> None:
        """以扫码模式拉起 QQ 环境（vbs 无 -q 参数），随后轮询登录状态出二维码。

        初始打开 agent、退出登录后点「启动 QQ 环境并扫码登录」都走这里。
        启动期间启动按钮置灰（环境由轮询接管），vbs 每次都会先杀旧引导进程再拉新。
        """
        self.btn_start_env.configure(state="disabled", text="环境启动中…")
        vbs = self._napcat_vbs_path()
        if not vbs:
            launcher = find_napcat_launcher()
            if launcher:
                try:
                    os.startfile(str(launcher))
                    self._log_status(f"正在启动（{launcher.name}）…")
                    if hasattr(self, "lbl_conn"):
                        self.lbl_conn.configure(text="环境启动中…", text_color="#e67e22")
                    self._schedule_login_poll()
                    return
                except OSError as e:
                    messagebox.showerror("启动失败", str(e))
                    self.btn_start_env.configure(state="normal", text="启动 QQ 环境并扫码登录")
                    return
            messagebox.showerror("缺少服务组件", "请确认安装包完整（缺少 tools 目录）")
            self.btn_start_env.configure(state="normal", text="启动 QQ 环境并扫码登录")
            return
        try:
            cmd = ["wscript", "//nologo", str(vbs)]
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
            self._log_status("正在准备环境（不影响您已打开的 QQ）…")
            if hasattr(self, "lbl_conn"):
                self.lbl_conn.configure(text="环境启动中…", text_color="#e67e22")
            self.lbl_login_status.configure(text="环境启动中，约 15~60 秒后可扫码", text_color="#e67e22")
            self.lbl_login_uin.configure(text="")
            self._schedule_login_poll()
            self.after(8000, self.refresh_login_qrcode)
            self.after(20000, self.refresh_login_qrcode)
            self.after(25000, self.refresh_status)
        except OSError as e:
            messagebox.showerror("启动失败", str(e))
            self.btn_start_env.configure(state="normal", text="启动 QQ 环境并扫码登录")

    def _schedule_login_poll(self) -> None:
        if self._login_poll_job:
            self.after_cancel(self._login_poll_job)
        self._login_poll_tick()

    def _login_poll_tick(self) -> None:
        self.refresh_login_qrcode(silent=True)
        self._login_poll_job = self.after(2500, self._login_poll_tick)

    def refresh_login_qrcode(self, silent: bool = False, force_refresh: bool = False, *_args) -> None:
        """取最新登录态与二维码（登录轮询与「刷新二维码」按钮共用）。

        silent=False + force_refresh=True 为按钮语义：向 QQ 请求一张新码（真刷新，
        不是重读界面）；轮询（silent=True）发现二维码过期（loginError 带「过期」）
        也自动刷一次（60 秒节流），不让界面停在过期码上。二维码图片一律按当前
        URL 现渲染——NapCat 缓存 qrcode.png 只在 QQ 生成新码时重写，过期后文件
        不更新，曾导致界面永远停在过期码、扫码失败且「刷新」看似无反应。
        """
        def work():
            import time as _t

            ping_ok = False
            try:
                napcat = find_napcat_dir(self.cfg)
                if not napcat:
                    self.after(
                        0,
                        lambda: self.lbl_login_status.configure(
                            text="未找到服务组件，请确认安装包完整（缺少 tools 目录）", text_color="#e74c3c"
                        ),
                    )
                    return
                webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
                ping_ok = webui.ping()
                if not ping_ok:
                    if not getattr(self, "_env_started", False):
                        self.after(0, self._auto_env_start)
                    else:
                        # 自动启动流程已跑完仍连不上（环境被关/启动失败）：
                        # 交还用户手动拉起，别让界面无限「环境启动中」
                        self.after(0, lambda: self.btn_start_env.configure(
                            state="normal", text="启动 QQ 环境并扫码登录"))
                    self.after(
                        0,
                        lambda: self.lbl_login_status.configure(
                            text="环境未就绪，正在重试…", text_color="#e67e22"
                        ),
                    )
                    return
                st = webui.check_login_status()
                if not st.is_login:
                    err_txt = st.login_error or ""
                    is_expired = "过期" in err_txt or "refresh" in err_txt.lower()
                    if force_refresh or is_expired:
                        now = _t.time()
                        if force_refresh or now - getattr(self, "_last_qr_auto_refresh", 0.0) > 60:
                            try:
                                webui.refresh_qrcode()
                                if not force_refresh:
                                    self._last_qr_auto_refresh = now
                            except Exception:
                                pass  # 刷新失败维持现状，等下一轮轮询再试
                            st = webui.check_login_status()
                url = st.qrcode_url
                if not st.is_login:
                    try:
                        url = webui.get_qrcode_url() or url
                    except Exception:
                        pass
                pil = qrcode_image(url)
                info = {}
                if st.is_login:
                    try:
                        info = webui.get_login_info()
                    except Exception:
                        pass
                self.after(0, lambda: self._apply_login_ui(st, pil, info, silent))
            except Exception as e:
                # 服务活着但取状态失败：可能是登录握手过渡、QQ 重建中、或瞬时波动——
                # 无法据此断言「手机已确认」，只给中性提示并继续自动重试
                # （真正的已确认态由 is_offline/登录成功呈现）
                if ping_ok:
                    self.after(0, lambda: self.lbl_login_status.configure(
                        text="登录服务处理中，正在自动重试…请稍候",
                        text_color="#e67e22"))
                else:
                    if not silent:
                        self.after(0, lambda e=e: messagebox.showerror("登录页刷新失败", str(e)))
                    self.after(
                        0,
                        lambda e=e: self.lbl_login_status.configure(
                            text="连接服务失败，正在重试…", text_color="#e67e22"),
                    )

        threading.Thread(target=work, daemon=True).start()

    def _apply_login_ui(self, st, pil, info: dict, silent: bool) -> None:
        if pil is not None:
            self._qr_ctk_image = pil_to_ctk(pil)
            self.lbl_qr.configure(image=self._qr_ctk_image, text="")
        elif not st.is_login:
            self.lbl_qr.configure(image=None, text="等待二维码\n环境就绪后自动显示")

        if st.is_login:
            nick = info.get("nick") or ""
            uin = info.get("uin") or ""
            self.lbl_login_status.configure(text="✓ 手机已确认，登录成功，正在进入主界面…", text_color="#2ecc71")
            self.lbl_login_uin.configure(text=f"{nick} ({uin})" if nick else str(uin))
            if hasattr(self, "lbl_conn"):
                self.lbl_conn.configure(text=f"已连接 QQ {uin}", text_color="#2ecc71")
            if self._login_poll_job:
                self.after_cancel(self._login_poll_job)
                self._login_poll_job = None
            # 登录成功：自动部署服务 → 进入主界面（后台执行，无需用户操作）
            self._after_login_auto()
        else:
            import time as _t
            now = _t.time()
            qr_url = st.qrcode_url
            if qr_url and qr_url != getattr(self, "_last_qr_url", None):
                self._last_qr_url = qr_url
                self._qr_show_ts = now
            wait_s = int(now - getattr(self, "_qr_show_ts", now))
            if st.is_offline:
                # QQ 已通过登录但还没上线：手机确认成功的过渡期
                text, color = "手机已确认，正在登录上线（约需半分钟），请稍候…", "#2ecc71"
            elif st.login_error:
                text, color = st.login_error, "#e74c3c"
            elif qr_url:
                # 扫码确认后 QQ 静默处理几十秒，期间旧二维码不变——
                # 用等待计时与文案让用户看到软件仍在工作，不会误以为卡死
                wait_txt = f"（二维码已等待 {wait_s} 秒）" if wait_s > 3 else ""
                text = f"请用手机 QQ 扫码，并在手机上点「确认登录」{wait_txt}"
                if wait_s > 75:
                    text += "　长时间无反应可点左侧「刷新二维码」重试"
                color = "#e67e22"
            else:
                text, color = "正在获取二维码…", "#e67e22"
            self.lbl_login_status.configure(text=text, text_color=color)
            self.lbl_login_uin.configure(
                text="扫码后登录处理约需 30~60 秒，期间界面无变化属正常，请耐心等待")
            # 环境健康（轮询能取到状态）时启动按钮不可用，防误触重启；
            # 由 ping 失败路径 / 退出登录流程放开
            self.btn_start_env.configure(state="disabled", text="启动 QQ 环境并扫码登录")
            if getattr(self, "_main_built", False) and self.view_main.winfo_ismapped():
                self._show_login_view()

    def _after_login_auto(self) -> None:
        """登录成功后的自动配置：部署服务 → 重载 → 同步设置 → 切主界面。只执行一次。"""
        if self._login_setup_done:
            return
        self._login_setup_done = True

        def work():
            try:
                ok, msg = deploy_plugin(self.cfg)
                if not ok:
                    raise RuntimeError(msg)
                napcat = find_napcat_dir(self.cfg)
                if napcat:
                    try:
                        webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
                        if webui.ping():
                            webui.reload_plugin(self.cfg.plugin_id or "napcat-plugin-cleaner")
                    except Exception:
                        pass  # 重载失败不阻断：下次重启服务自动生效
                try:
                    self.client.sync_plugin_config()
                except Exception:
                    pass
                self.after(0, self._enter_main_after_login)
            except Exception as e:
                def fail(err=e):
                    self._login_setup_done = False
                    self.lbl_login_status.configure(
                        text=f"进入失败（点「刷新二维码」重试）: {err}", text_color="#e74c3c")
                self.after(0, fail)

        threading.Thread(target=work, daemon=True).start()

    def _enter_main_after_login(self) -> None:
        self.lbl_login_status.configure(text="已登录", text_color="#2ecc71")
        self._show_main_view()
        self.refresh_status()
        self.refresh_monitor()

    def _build_more_tab(self) -> None:
        """「更多」页（放最后）：服务状态 + 设置 + 使用说明，合并不常用菜单。"""
        wrap = ctk.CTkScrollableFrame(self.tab_more)
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        # ---- ① 服务状态 ----
        card = ctk.CTkFrame(wrap)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 6))
        ctk.CTkLabel(card, text="服务状态", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=10, pady=2)
        rows = [
            ("服务地址", "napcat_base"),
            ("当前 QQ", "self_uin"),
            ("缓存红包数", "record_count"),
            ("自动收红包", "auto_grab"),
            ("自动查详情", "auto_pull"),
        ]
        self.status_vars: dict[str, ctk.StringVar] = {}
        for i, (label, key) in enumerate(rows):
            r, c = divmod(i, 2)
            ctk.CTkLabel(grid, text=label + "：").grid(row=r, column=c * 2, sticky="w", padx=(0, 6), pady=4)
            var = ctk.StringVar(value="-")
            self.status_vars[key] = var
            ctk.CTkLabel(grid, textvariable=var, anchor="w").grid(
                row=r, column=c * 2 + 1, sticky="w", padx=(0, 18), pady=4)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 8))
        ctk.CTkButton(btn_row, text="同步设置到服务", command=self.sync_settings).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="重新部署服务", command=self.deploy_plugin_action).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="清空运行日志", command=self.clear_status_log).pack(side="left", padx=6)

        self.log_status = ctk.CTkTextbox(card, height=110)
        self.log_status.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._log_status("欢迎使用。登录后自动同步服务，页面全程自动运行。")

        # ---- ② 设置（一般无需修改）----
        card = ctk.CTkFrame(wrap)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=6)
        ctk.CTkLabel(card, text="设置（一般无需修改）", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=10, pady=2)
        grid.grid_columnconfigure(1, weight=1)
        fields: list[tuple[str, str, str]] = [
            ("服务地址", "napcat_base", "http://127.0.0.1:6099（勿改）"),
            ("监听群号（逗号分隔）", "watch_groups", "留空=全部群"),
            ("服务插件目录", "napcat_plugins_dir", "留空则自动猜测"),
        ]
        self.setting_entries: dict[str, ctk.CTkEntry] = {}
        for i, (label, key, ph) in enumerate(fields):
            ctk.CTkLabel(grid, text=label).grid(row=i, column=0, padx=(0, 8), pady=6, sticky="w")
            e = ctk.CTkEntry(grid, placeholder_text=ph)
            e.grid(row=i, column=1, padx=8, pady=6, sticky="ew")
            val = getattr(self.cfg, key, "")
            if val:
                e.insert(0, str(val))
            self.setting_entries[key] = e
            if key == "napcat_plugins_dir":
                ctk.CTkButton(grid, text="浏览…", width=70, command=self._browse_plugins_dir).grid(
                    row=i, column=2, padx=4)

        row = len(fields)
        self.sw_auto_grab = ctk.CTkSwitch(grid, text="自动收红包")
        self.sw_auto_grab.grid(row=row, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.auto_grab:
            self.sw_auto_grab.select()

        self.sw_grab_self = ctk.CTkSwitch(grid, text="允许领取自己发的包（拼手气）")
        self.sw_grab_self.grid(row=row + 1, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.grab_self:
            self.sw_grab_self.select()

        self.sw_auto_detail = ctk.CTkSwitch(grid, text="自动查领取详情")
        self.sw_auto_detail.grid(row=row + 2, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.auto_pull_detail:
            self.sw_auto_detail.select()

        self.sw_password = ctk.CTkSwitch(grid, text="自动发口令（口令红包）")
        self.sw_password.grid(row=row + 3, column=1, sticky="w", padx=8, pady=4)
        if self.cfg.handle_password:
            self.sw_password.select()

        ctk.CTkLabel(grid, text="领取延迟(ms)").grid(row=row + 4, column=0, padx=(0, 8), pady=6, sticky="w")
        delay_frame = ctk.CTkFrame(grid, fg_color="transparent")
        delay_frame.grid(row=row + 4, column=1, sticky="w", padx=8)
        self.entry_delay_min = ctk.CTkEntry(delay_frame, width=70)
        self.entry_delay_min.insert(0, str(self.cfg.delay_min_ms))
        self.entry_delay_min.pack(side="left")
        ctk.CTkLabel(delay_frame, text=" ~ ").pack(side="left")
        self.entry_delay_max = ctk.CTkEntry(delay_frame, width=70)
        self.entry_delay_max.insert(0, str(self.cfg.delay_max_ms))
        self.entry_delay_max.pack(side="left")

        ctk.CTkButton(grid, text="保存设置", command=self.save_settings).grid(
            row=row + 5, column=1, sticky="w", padx=8, pady=12)

        # ---- ③ 使用说明 ----
        card = ctk.CTkFrame(wrap)
        card.grid(row=2, column=0, sticky="ew", padx=4, pady=6)
        ctk.CTkLabel(card, text="使用说明", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(8, 2))
        text = ctk.CTkTextbox(card, height=320)
        text.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")

    # ---------- 群管理（表格） ----------

    def _build_groups_tab(self) -> None:
        f = self.tab_groups
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        self.entry_add_group = ctk.CTkEntry(row, width=150, placeholder_text="输入群号")
        self.entry_add_group.pack(side="left")
        self.entry_add_group.bind("<Return>", lambda _e: self._groups_add())
        ctk.CTkButton(row, text="＋ 添加群", width=80, command=self._groups_add).pack(side="left", padx=4)
        ctk.CTkButton(row, text="刷新", width=64, command=self._groups_refresh).pack(side="left", padx=4)
        ctk.CTkButton(row, text="开启监控", width=84, command=lambda: self._groups_set_watch(True)).pack(side="left", padx=4)
        ctk.CTkButton(row, text="关闭监控", width=84, command=lambda: self._groups_set_watch(False)).pack(side="left", padx=4)
        ctk.CTkButton(row, text="同步会员", width=84, command=self._groups_sync_members).pack(side="left", padx=4)
        ctk.CTkButton(row, text="上报群信息", width=92, command=self._groups_report).pack(side="left", padx=4)
        ctk.CTkButton(row, text="删除", width=64, fg_color="#c0392b", hover_color="#a93226",
                      command=self._groups_delete).pack(side="left", padx=4)
        ctk.CTkButton(row, text="导入我的群", width=92, command=self._groups_import).pack(side="left", padx=4)
        self.lbl_groups_info = ctk.CTkLabel(row, text="", text_color="gray")
        self.lbl_groups_info.pack(side="left", padx=10)

        tip = ctk.CTkLabel(
            f,
            text="可多选（Ctrl/Shift 点击）。群号只需在这里维护一次：监控、会员、游戏玩法都从这里选。",
            text_color="gray", font=ctk.CTkFont(size=11),
        )
        tip.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 2))

        frame = tk.Frame(f, highlightthickness=1, highlightbackground="#CBD5E1")
        frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        cols = ("gid", "name", "count", "watch", "ctime")
        self.tree_groups = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, w, t in [
            ("gid", 120, "群号"),
            ("name", 180, "群名"),
            ("count", 70, "人数"),
            ("watch", 80, "监控"),
            ("ctime", 150, "创建时间"),
        ]:
            self.tree_groups.heading(c, text=t)
            self.tree_groups.column(c, width=w)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_groups.yview)
        self.tree_groups.configure(yscrollcommand=sb.set)
        self.tree_groups.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree_groups.tag_configure("watched", foreground="#2563EB")
        self.tree_groups.tag_configure("banned", foreground="#e74c3c")
        self._groups_rows: list[dict] = []
        self.after(200, self._groups_refresh)

    def _backend_client(self) -> "HbjfClient | None":
        """总后台客户端；未配置地址/密钥时返回 None（提示未对接）。"""
        if not (self.cfg.backend_base and self.cfg.backend_api_key):
            return None
        return HbjfClient(self.cfg.backend_base, self.cfg.backend_api_key,
                          token=self.cfg.executor_token)

    def _groups_refresh(self) -> None:
        if not hasattr(self, "tree_groups"):
            return

        def work():
            hidden = set(self._hidden_groups())
            rows: dict[str, dict] = {}
            for gid in self._parse_watch_groups():
                if gid in hidden:
                    continue
                rows[gid] = {"group_id": gid, "group_name": "", "member_count": "",
                             "create_time": "", "status": ""}
            client = self._backend_client()
            if client:
                try:
                    for g in client.list_groups():
                        gid = str(g.get("group_id") or "")
                        if not gid or gid in hidden:
                            continue
                        prev = rows.get(gid, {})
                        rows[gid] = {
                            "group_id": gid,
                            "group_name": g.get("group_name") or prev.get("group_name") or "",
                            "member_count": g.get("member_count") or "",
                            "create_time": g.get("create_time") or "",
                            "status": g.get("status") or "",
                        }
                except BackendError as e:
                    self.after(0, lambda e=e: self.lbl_groups_info.configure(
                        text=f"总后台群列表拉取失败（不影响本机群）: {e}", text_color="#e67e22"))
            ordered = sorted(rows.values(), key=lambda r: r["group_id"])
            self.after(0, lambda: self._groups_fill(ordered))

        threading.Thread(target=work, daemon=True).start()

    def _groups_fill(self, rows: list[dict]) -> None:
        hidden = set(self._hidden_groups())
        rows = [r for r in rows if str(r.get("group_id") or "") not in hidden]
        watch = set(self._parse_watch_groups())
        for iid in self.tree_groups.get_children():
            self.tree_groups.delete(iid)
        self._groups_rows = rows
        for r in rows:
            gid = str(r.get("group_id") or "")
            tags = ["watched"] if gid in watch else []
            if str(r.get("status") or "") == "banned":
                tags.append("banned")
            self.tree_groups.insert("", "end", iid=gid, values=(
                gid,
                r.get("group_name") or "（未上报）",
                r.get("member_count") or "",
                "● 监控中" if gid in watch else "—",
                r.get("create_time") or "",
            ), tags=tags)
        self.lbl_groups_info.configure(text=f"共 {len(rows)} 个群（蓝色=监控中，红色=已被总后台封禁）")

    def _groups_selected(self) -> list[str]:
        if not hasattr(self, "tree_groups"):
            return []
        return [str(iid) for iid in self.tree_groups.selection()]

    def _hidden_groups(self) -> list[str]:
        return [x.strip() for x in self.cfg.hidden_groups.replace("，", ",").split(",") if x.strip()]

    def _groups_delete(self) -> None:
        """删除选中群：从本机列表移除（不再展示、退出监控与玩法；总后台记录保留）。"""
        sels = self._groups_selected()
        if not sels:
            self.lbl_groups_info.configure(text="请先在表格里选中群（可多选）", text_color="#e67e22")
            return
        hidden = set(self._hidden_groups()) | set(sels)
        self.cfg.hidden_groups = ",".join(sorted(hidden))
        self.cfg.watch_groups = ",".join(g for g in self._parse_watch_groups() if g not in sels)
        self.cfg.play_group_id = ",".join(g for g in self.cfg.play_group_list() if g not in sels)
        save_config(self.cfg)
        self.client = PluginClient(self.cfg)
        self._groups_rows = [r for r in self._groups_rows if str(r.get("group_id")) not in sels]
        self._groups_fill(self._groups_rows)
        self._members_refresh_groups()
        try:
            if hasattr(self, "play_tab"):
                self.play_tab._refresh_group_checkboxes()
        except Exception:
            pass
        threading.Thread(target=lambda: self.client.sync_plugin_config(), daemon=True).start()
        self.lbl_groups_info.configure(text=f"已删除 {len(sels)} 个群（监控与玩法勾选已同步移除）",
                                       text_color="#2ecc71")

    def _groups_import(self) -> None:
        """一键导入当前登录 QQ 的全部群（免输群号，选中后即可开启监控）。"""
        if not hasattr(self, "tree_groups"):
            return

        def work():
            try:
                groups = self.client.get_group_list()
            except Exception as e:
                self.after(0, lambda e=e: self.lbl_groups_info.configure(
                    text=f"拉取群列表失败: {e}", text_color="#e74c3c"))
                return
            known = {str(r.get("group_id")) for r in self._groups_rows}
            hidden = set(self._hidden_groups())
            added = 0
            for g in groups:
                gid = str(g.get("group_id") or "")
                if not gid or gid in known or gid in hidden:
                    continue
                self._groups_rows.append({
                    "group_id": gid,
                    "group_name": g.get("group_name") or "",
                    "member_count": g.get("member_count") or "",
                    "create_time": "",
                    "status": "",
                })
                added += 1
            self.after(0, lambda: self._groups_fill(self._groups_rows))
            self.after(0, lambda a=added: self.lbl_groups_info.configure(
                text=f"已导入 {a} 个新群（登录 QQ 的全部群，共 {len(groups)} 个），选中后点「开启监控」",
                text_color="#2ecc71"))
            self.after(0, self._members_refresh_groups)

        threading.Thread(target=work, daemon=True).start()

    def _groups_add(self) -> None:
        gid = self.entry_add_group.get().strip().replace("，", ",")
        parts = [g.strip() for g in gid.split(",") if g.strip().isdigit()]
        if not parts:
            self.lbl_groups_info.configure(text="请先输入群号再添加", text_color="#e67e22")
            return
        known = {str(r.get("group_id")) for r in self._groups_rows}
        for g in parts:
            if g not in known:
                self._groups_rows.append({"group_id": g, "group_name": "", "member_count": "",
                                          "create_time": "", "status": ""})
        self.entry_add_group.delete(0, "end")
        self._groups_fill(self._groups_rows)
        for g in parts:
            self._groups_fetch_meta(g)
        self._members_refresh_groups()

    def _groups_fetch_meta(self, gid: str) -> None:
        """后台拉群名/人数/创建时间回填表格，并顺带上报总后台群信息。"""
        def work():
            try:
                data = self.client.get_group_members(gid, no_cache=True) or {}
            except Exception:
                return
            if data:
                self.after(0, lambda: self._groups_apply_meta(gid, data))
            client = self._backend_client()
            if client and data:
                try:
                    client.upsert_group(gid, group_name=str(data.get("group_name") or ""),
                                        member_count=str(data.get("member_count") or ""),
                                        create_time=group_create_time_str(data))
                except BackendError:
                    pass  # 上报失败不打断

        threading.Thread(target=work, daemon=True).start()

    def _groups_apply_meta(self, gid: str, data: dict) -> None:
        ctime = group_create_time_str(data)
        for r in self._groups_rows:
            if str(r.get("group_id")) == gid:
                if data.get("group_name"):
                    r["group_name"] = data.get("group_name")
                if data.get("member_count"):
                    r["member_count"] = data.get("member_count")
                if ctime:
                    r["create_time"] = ctime
        self._groups_fill(self._groups_rows)

    def _groups_set_watch(self, on: bool) -> None:
        sels = self._groups_selected()
        if not sels:
            self.lbl_groups_info.configure(text="请先在表格里选中群（可多选）", text_color="#e67e22")
            return
        watch = set(self._parse_watch_groups())
        for g in sels:
            if on:
                watch.add(g)
            else:
                watch.discard(g)
        self.cfg.watch_groups = ",".join(sorted(watch))
        save_config(self.cfg)
        if "watch_groups" in getattr(self, "setting_entries", {}):
            e = self.setting_entries["watch_groups"]
            e.delete(0, "end")
            e.insert(0, self.cfg.watch_groups)
        if hasattr(self, "entry_monitor_group"):
            self.entry_monitor_group.delete(0, "end")
            self.entry_monitor_group.insert(0, self.cfg.watch_groups)
        self.client = PluginClient(self.cfg)
        self._groups_fill(self._groups_rows)

        def work():
            try:
                self.client.sync_plugin_config()
                self.after(0, lambda: self.lbl_groups_info.configure(
                    text=f"已{'开启' if on else '关闭'} {len(sels)} 个群的监控", text_color="#2ecc71"))
            except Exception as e:
                self.after(0, lambda e=e: self.lbl_groups_info.configure(
                    text=f"同步监控失败: {e}", text_color="#e74c3c"))

        threading.Thread(target=work, daemon=True).start()

    def _groups_sync_members(self) -> None:
        sels = self._groups_selected()
        if not sels:
            self.lbl_groups_info.configure(text="请先在表格里选中群（可多选）", text_color="#e67e22")
            return
        client = self._backend_client()
        if not client:
            self.lbl_groups_info.configure(text="未配置总后台（「总后台对接」页填写后即可同步）", text_color="#e67e22")
            return
        self.lbl_groups_info.configure(text=f"正在同步 {len(sels)} 个群的会员…", text_color="#3498db")

        def work():
            total_added = 0
            for gid in sels:
                try:
                    res = run_member_sync(
                        client, gid,
                        lambda g: (self.client.get_group_members(g, no_cache=True) or {}).get("members") or [],
                        lambda g: self.client.get_group_members(g, no_cache=True) or {},
                    )
                    total_added += int(res.get("added") or 0)
                    warn = res.get("warning") or ""
                    self.after(0, lambda g=gid, r=res, w=warn: self._log_status(
                        f"群 {g} 会员同步：成员 {r.get('total')}，新增 {r.get('added')}"
                        + (f"；注意：{w}" if w else "")))
                except (ExecutorBanned, OperatorDisabled) as e:
                    self.after(0, lambda e=e: self.lbl_groups_info.configure(
                        text=f"同步停摆：{e}", text_color="#e74c3c"))
                    return
                except BackendError as e:
                    self.after(0, lambda g=gid, e=e: self._log_status(f"群 {g} 同步失败: {e}"))
            self.after(0, lambda: self.lbl_groups_info.configure(
                text=f"同步完成，共新增会员 {total_added}", text_color="#2ecc71"))
            self.after(0, self._groups_refresh)

        threading.Thread(target=work, daemon=True).start()

    def _groups_report(self) -> None:
        sels = self._groups_selected()
        if not sels:
            self.lbl_groups_info.configure(text="请先在表格里选中群（可多选）", text_color="#e67e22")
            return
        client = self._backend_client()
        if not client:
            self.lbl_groups_info.configure(text="未配置总后台（「总后台对接」页填写后即可上报）", text_color="#e67e22")
            return

        def work():
            ok = 0
            for gid in sels:
                try:
                    data = self.client.get_group_members(gid, no_cache=True) or {}
                    if not data:
                        continue
                    client.upsert_group(gid, group_name=str(data.get("group_name") or ""),
                                        member_count=str(data.get("member_count") or ""),
                                        create_time=group_create_time_str(data))
                    ok += 1
                except BackendError as e:
                    self.after(0, lambda g=gid, e=e: self._log_status(f"上报群 {g} 失败: {e}"))
                except Exception:
                    pass
            self.after(0, lambda: self.lbl_groups_info.configure(
                text=f"已上报 {ok}/{len(sels)} 个群的群信息（含创建时间）", text_color="#2ecc71"))
            self.after(0, self._groups_refresh)

        threading.Thread(target=work, daemon=True).start()

    # ---------- 会员（表格） ----------

    def _build_members_tab(self) -> None:
        f = self.tab_members
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(2, weight=1)

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        ctk.CTkLabel(row, text="群：").pack(side="left")
        self.combo_members_tab_group = ctk.CTkComboBox(row, values=["（先去群管理添加群）"], width=200)
        self.combo_members_tab_group.pack(side="left", padx=4)
        ctk.CTkButton(row, text="加载成员", width=88, command=self._members_load).pack(side="left", padx=4)
        ctk.CTkButton(row, text="全部同步", width=88, command=self._members_sync_all).pack(side="left", padx=4)
        self.lbl_members_tab_info = ctk.CTkLabel(row, text="", text_color="gray")
        self.lbl_members_tab_info.pack(side="left", padx=10)

        row2 = ctk.CTkFrame(f, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 2))
        self.entry_manual_points = ctk.CTkEntry(row2, width=90, placeholder_text="金额")
        self.entry_manual_points.pack(side="left")
        ctk.CTkButton(row2, text="上分", width=70, fg_color="#27ae60", hover_color="#1e8449",
                      command=lambda: self._members_manual("up")).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="下分", width=70, fg_color="#c0392b", hover_color="#a93226",
                      command=lambda: self._members_manual("down")).pack(side="left", padx=4)
        ctk.CTkLabel(row2, text="选中会员后操作，积分自动同步到总后台",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left", padx=8)
        self.entry_ban_minutes = ctk.CTkEntry(row2, width=70, placeholder_text="禁言分钟")
        self.entry_ban_minutes.insert(0, "10")
        self.entry_ban_minutes.pack(side="left", padx=(12, 0))
        ctk.CTkButton(row2, text="禁言", width=64, command=self._members_ban).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="踢出群聊", width=84, fg_color="#c0392b", hover_color="#a93226",
                      command=self._members_kick).pack(side="left", padx=4)

        frame = tk.Frame(f, highlightthickness=1, highlightbackground="#CBD5E1")
        frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        cols = ("uin", "name", "role", "points")
        self.tree_members_tab = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, w, t in [
            ("uin", 120, "QQ"),
            ("name", 180, "昵称"),
            ("role", 90, "身份"),
            ("points", 100, "积分"),
        ]:
            self.tree_members_tab.heading(c, text=t)
            self.tree_members_tab.column(c, width=w)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_members_tab.yview)
        self.tree_members_tab.configure(yscrollcommand=sb.set)
        self.tree_members_tab.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._members_rows: list[dict] = []
        self.after(500, self._members_refresh_groups)

    def _members_groups(self) -> list[str]:
        hidden = set(self._hidden_groups())
        groups = [str(r.get("group_id")) for r in getattr(self, "_groups_rows", [])
                  if str(r.get("group_id")) not in hidden]
        for g in self._parse_watch_groups():
            if g not in groups and g not in hidden:
                groups.append(g)
        return groups

    def _members_refresh_groups(self) -> None:
        if not hasattr(self, "combo_members_tab_group"):
            return
        groups = self._members_groups()
        self.combo_members_tab_group.configure(values=groups or ["（先去群管理添加群）"])
        if groups:
            cur = self.combo_members_tab_group.get()
            if cur not in groups:
                self.combo_members_tab_group.set(groups[0])

    def _members_group(self) -> str:
        if not hasattr(self, "combo_members_tab_group"):
            return ""
        g = (self.combo_members_tab_group.get() or "").strip()
        return g if g.isdigit() else ""

    def _members_load(self) -> None:
        gid = self._members_group()
        if not gid:
            self.lbl_members_tab_info.configure(text="请先在「群管理」添加群并选择", text_color="#e67e22")
            return
        self.lbl_members_tab_info.configure(text=f"正在加载群 {gid} 成员…", text_color="gray")

        def work():
            try:
                data = self.client.get_group_members(gid, no_cache=True) or {}
                members = data.get("members") or []
            except Exception as e:
                self.after(0, lambda e=e: self.lbl_members_tab_info.configure(
                    text=f"加载失败: {e}", text_color="#e74c3c"))
                return
            points: dict[str, dict] = {}
            client = self._backend_client()
            if client:
                qqs = [str(m.get("uin") or "") for m in members if str(m.get("uin") or "").isdigit()]
                for i in range(0, len(qqs), 500):
                    try:
                        for r in client.query_points_batch(qqs[i:i + 500]):
                            points[str(r.get("qq"))] = r
                    except BackendError:
                        break
            self.after(0, lambda: self._members_fill(gid, data, members, points))

        threading.Thread(target=work, daemon=True).start()

    def _members_fill(self, gid: str, data: dict, members: list[dict], points: dict[str, dict]) -> None:
        for iid in self.tree_members_tab.get_children():
            self.tree_members_tab.delete(iid)
        self._members_rows = members
        for m in members:
            uin = str(m.get("uin") or "")
            p = points.get(uin) or {}
            pts = ""
            if p.get("exists"):
                pts = str(p.get("points") if p.get("points") is not None else 0)
            elif p:
                pts = "未建档"
            self.tree_members_tab.insert("", "end", values=(
                uin,
                m.get("nickname") or m.get("card") or "",
                m.get("role_text") or m.get("role") or "",
                pts,
            ))
        gname = data.get("group_name") or ""
        title = f"群 {gid}" + (f" · {gname}" if gname else "") + f" · 共 {len(members)} 人"
        if gname and not points:
            title += "（未配置总后台，不显示积分）"
        self.lbl_members_tab_info.configure(text=title, text_color="#2ecc71")

    def _members_selected_qqs(self) -> list[str]:
        """会员表格选中的 QQ 列表。"""
        if not hasattr(self, "tree_members_tab"):
            return []
        return [str(self.tree_members_tab.item(i, "values")[0])
                for i in self.tree_members_tab.selection()]

    def _members_kick(self) -> None:
        """踢出群聊（登录 QQ 须为群主/管理员；操作前确认）。"""
        gid = self._members_group()
        qqs = self._members_selected_qqs()
        if not gid or not qqs:
            self.lbl_members_tab_info.configure(text="请先选择群并选中成员", text_color="#e67e22")
            return
        if not messagebox.askyesno("确认踢出", f"确定把选中的 {len(qqs)} 个成员踢出群 {gid}？"):
            return
        self.lbl_members_tab_info.configure(text=f"正在踢出 {len(qqs)} 个成员…", text_color="#3498db")

        def work():
            ok = 0
            for qq in qqs:
                try:
                    self.client.group_kick(gid, qq)
                    ok += 1
                except Exception as e:
                    self.after(0, lambda q=qq, e=e: self._log_status(f"踢出 {q} 失败: {e}"))
            self.after(0, lambda: self.lbl_members_tab_info.configure(
                text=f"已踢出 {ok}/{len(qqs)} 个成员", text_color="#2ecc71"))
            self.after(0, self._members_load)

        threading.Thread(target=work, daemon=True).start()

    def _members_ban(self) -> None:
        """禁言（分钟，默认 10；0=解除禁言）。"""
        gid = self._members_group()
        qqs = self._members_selected_qqs()
        if not gid or not qqs:
            self.lbl_members_tab_info.configure(text="请先选择群并选中成员", text_color="#e67e22")
            return
        try:
            minutes = int(self.entry_ban_minutes.get().strip())
            if minutes < 0:
                raise ValueError
        except ValueError:
            self.lbl_members_tab_info.configure(text="禁言分钟需为非负整数", text_color="#e67e22")
            return
        if not messagebox.askyesno("确认禁言", f"确定禁言选中的 {len(qqs)} 个成员 {minutes} 分钟？"):
            return
        self.lbl_members_tab_info.configure(text=f"正在禁言 {len(qqs)} 个成员 {minutes} 分钟…",
                                            text_color="#3498db")

        def work():
            ok = 0
            for qq in qqs:
                try:
                    self.client.group_ban(gid, qq, minutes * 60)
                    ok += 1
                except Exception as e:
                    self.after(0, lambda q=qq, e=e: self._log_status(f"禁言 {q} 失败: {e}"))
            self.after(0, lambda: self.lbl_members_tab_info.configure(
                text=f"已禁言 {ok}/{len(qqs)} 个成员（{minutes} 分钟）", text_color="#2ecc71"))
            self.after(0, self._members_load)

        threading.Thread(target=work, daemon=True).start()

    def _members_manual(self, action: str) -> None:
        """会员页手动上分/下分 → 同步总后台（可多选会员）。"""
        if not hasattr(self, "tree_members_tab"):
            return
        sels = self.tree_members_tab.selection()
        if not sels:
            self.lbl_members_tab_info.configure(text="请先在表格里选中会员（可多选）", text_color="#e67e22")
            return
        try:
            amount = int(self.entry_manual_points.get().strip())
            if amount <= 0:
                raise ValueError
        except ValueError:
            self.lbl_members_tab_info.configure(text="请填写正整数的金额", text_color="#e67e22")
            return
        client = self._backend_client()
        if not client:
            self.lbl_members_tab_info.configure(text="未配置总后台（「总后台对接」页填写后即可操作）", text_color="#e67e22")
            return
        qqs = [str(self.tree_members_tab.item(i, "values")[0]) for i in sels]
        label = "上分" if action == "up" else "下分"
        self.lbl_members_tab_info.configure(text=f"正在{label} {len(qqs)} 个会员 {amount} 积分…", text_color="#3498db")

        def work():
            ok = 0
            for qq in qqs:
                try:
                    fn = client.up_points if action == "up" else client.down_points
                    fn(qq, amount, reason=f"agent会员页手动{label}")
                    ok += 1
                except (ExecutorBanned, OperatorDisabled) as e:
                    self.after(0, lambda e=e: self.lbl_members_tab_info.configure(
                        text=f"操作停摆：{e}", text_color="#e74c3c"))
                    return
                except BackendError as e:
                    self.after(0, lambda q=qq, e=e: self._log_status(f"会员 {q} {label}失败: {e}"))
            self.after(0, lambda: self.lbl_members_tab_info.configure(
                text=f"已{label} {ok}/{len(qqs)} 个会员各 {amount} 积分", text_color="#2ecc71"))
            self.after(0, self._members_load)

        threading.Thread(target=work, daemon=True).start()

    def _members_sync_all(self) -> None:
        gid = self._members_group()
        if not gid:
            self.lbl_members_tab_info.configure(text="请先选择群", text_color="#e67e22")
            return
        client = self._backend_client()
        if not client:
            self.lbl_members_tab_info.configure(text="未配置总后台（「总后台对接」页填写后即可同步）", text_color="#e67e22")
            return
        self.lbl_members_tab_info.configure(text=f"正在同步群 {gid} 会员…", text_color="#3498db")

        def work():
            try:
                res = run_member_sync(
                    client, gid,
                    lambda g: (self.client.get_group_members(g, no_cache=True) or {}).get("members") or [],
                    lambda g: self.client.get_group_members(g, no_cache=True) or {},
                )
                warn = res.get("warning") or ""
                self.after(0, lambda r=res, w=warn: self.lbl_members_tab_info.configure(
                    text=f"同步完成：成员 {r.get('total')}，新增 {r.get('added')}"
                         + (f"；注意：{w}" if w else ""),
                    text_color="#e67e22" if warn else "#2ecc71"))
            except (ExecutorBanned, OperatorDisabled) as e:
                self.after(0, lambda e=e: self.lbl_members_tab_info.configure(
                    text=f"同步停摆：{e}", text_color="#e74c3c"))
            except BackendError as e:
                self.after(0, lambda e=e: self.lbl_members_tab_info.configure(
                    text=f"同步失败: {e}", text_color="#e74c3c"))
            self.after(0, self._members_load)

        threading.Thread(target=work, daemon=True).start()


    def _build_approve_tab(self) -> None:
        """积分审批：群内上分/下分申请 → 审批 → 同步总后台。"""
        self.approve_tab = ApprovalTab(
            self.tab_approve, cfg=self.cfg, client_factory=self._backend_client)
        self.approve_tab.pack(fill="both", expand=True)
        # 玩法回调服务把 /play/approve 事件转给审批页
        self.play_tab.on_approve = self.approve_tab.push

    def _build_monitor_tab(self) -> None:
        f = self.tab_monitor
        f.grid_columnconfigure(0, weight=1)
        f.grid_rowconfigure(1, weight=2)
        f.grid_rowconfigure(2, weight=1)
        f.grid_rowconfigure(3, weight=1)

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

        # 历史查询（并入本页底部，不再单独成页）
        query_box = ctk.CTkFrame(f)
        query_box.grid(row=3, column=0, sticky="nsew", padx=8, pady=(4, 8))
        qrow = ctk.CTkFrame(query_box, fg_color="transparent")
        qrow.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))
        ctk.CTkLabel(qrow, text="历史查询：").pack(side="left")
        ctk.CTkLabel(qrow, text="群号").pack(side="left", padx=(12, 4))
        self.entry_q_group = ctk.CTkEntry(qrow, width=120, placeholder_text="必填")
        self.entry_q_group.pack(side="left", padx=4)
        ctk.CTkLabel(qrow, text="开始").pack(side="left", padx=(8, 4))
        self.entry_q_start = ctk.CTkEntry(qrow, width=150)
        self.entry_q_start.insert(0, (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"))
        self.entry_q_start.pack(side="left", padx=4)
        ctk.CTkLabel(qrow, text="结束").pack(side="left", padx=(8, 4))
        self.entry_q_end = ctk.CTkEntry(qrow, width=150)
        self.entry_q_end.insert(0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.entry_q_end.pack(side="left", padx=4)
        self.chk_refresh = ctk.CTkCheckBox(qrow, text="重新拉取详情")
        self.chk_refresh.deselect()
        self.chk_refresh.pack(side="left", padx=8)
        ctk.CTkButton(qrow, text="查询", width=64, command=self.run_query).pack(side="left", padx=4)
        ctk.CTkButton(qrow, text="导出表格", width=80, command=self.export_query_csv).pack(side="left", padx=4)
        self.txt_query_result = ctk.CTkTextbox(query_box, height=110)
        self.txt_query_result.grid(row=1, column=0, sticky="ew", padx=8, pady=4)

    def _log_status(self, msg: str) -> None:
        if not hasattr(self, "log_status"):
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_status.insert("end", f"[{ts}] {msg}\n")
        self.log_status.see("end")

    def _apply_cfg_from_settings_ui(self) -> None:
        if not hasattr(self, "setting_entries"):
            return
        e = self.setting_entries.get("napcat_base")
        if e is not None and e.get().strip():
            self.cfg.napcat_base = e.get().strip()
        e = self.setting_entries.get("watch_groups")
        if e is not None:
            self.cfg.watch_groups = e.get().strip()
        e = self.setting_entries.get("napcat_plugins_dir")
        if e is not None:
            self.cfg.napcat_plugins_dir = e.get().strip()
        if hasattr(self, "sw_auto_grab"):
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
                self.after(0, lambda: self._log_status("已同步设置到服务"))
            except Exception as e:
                self.after(0, lambda e=e: messagebox.showerror("同步失败", str(e)))

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
            if hasattr(self, "lbl_conn"):
                self.lbl_conn.configure(text=f"已连接 QQ {st.self_uin or '?'}", text_color="#2ecc71")
            if hasattr(self, "status_vars"):
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
            if hasattr(self, "lbl_conn"):
                self.lbl_conn.configure(text="未连接服务", text_color="#e74c3c")
            err = st.raw.get("error", "")
            if _is_startup_noise(err):
                err = "环境准备中，请稍候（可点击右上角「刷新」）"
            self._log_status(f"连接失败: {err}")
            self._schedule_login_poll()
            self.refresh_login_qrcode(silent=True)

    def _parse_watch_groups(self, raw: str | None = None) -> list[str]:
        if raw is None:
            raw = self.entry_monitor_group.get() if hasattr(self, "entry_monitor_group") else self.cfg.watch_groups
        text = raw
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
                self.after(0, lambda e=e: messagebox.showerror("同步失败", str(e)))

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
            self._log_wait_throttled("等待服务就绪…")
            return
        if not hasattr(self, "tree_packets"):
            return  # 主界面尚未构建

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
                            "服务尚未就绪，请稍候（可点击右上角「刷新」）"
                        ),
                    )
                    self.after(0, self.refresh_status)
                else:
                    self.after(0, lambda e=e: self._log_status(f"监控刷新失败: {e}"))

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
                self.after(0, lambda e=e: messagebox.showerror("清空失败", str(e)))
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
                self.after(0, lambda e=e: self._log_status(f"群成员加载失败: {e}"))

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
                self.after(0, lambda e=e: messagebox.showerror("失败", str(e)))

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
                self.after(0, lambda e=e: self._set_query_result(f"查询失败: {e}"))
            except Exception as e:
                self.after(0, lambda e=e: self._set_query_result(f"错误: {e}"))

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
        d = filedialog.askdirectory(title="选择服务插件目录")
        if d:
            self.setting_entries["napcat_plugins_dir"].delete(0, "end")
            self.setting_entries["napcat_plugins_dir"].insert(0, d)

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
        # agent 启动（登录进主界面）即自动开启心跳+远程命令，无需手动「启动对接」
        self.backend_tab.auto_start()

    def _build_play_tab(self) -> None:
        """游戏玩法（群聊自动回复引擎）：独立模块挂载，见 play_tab.PlayTab。"""
        self.play_tab = PlayTab(
            self.tab_play,
            cfg=self.cfg,
            client=self.client,
            save_config=save_config,
            app_version=APP_VERSION,
        )
        self.play_tab.pack(fill="both", expand=True)

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
        # 停掉玩法回调服务 + 消息 worker
        try:
            if getattr(self, "play_tab", None):
                self.play_tab.shutdown()
        except Exception:
            pass
        # 停掉审批 worker
        try:
            if getattr(self, "approve_tab", None):
                self.approve_tab.shutdown()
        except Exception:
            pass
        self.destroy()
