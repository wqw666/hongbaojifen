"""第 7 个 Tab「总后台对接」：执行器连接总后台的界面挂载（最小侵入式）。

能力（对应 integration 包）：
- 连接设置：总后台地址 / X-Api-Key / 执行器 token / 名称 / 心跳间隔
- 测试连接、保存到 AppConfig；SyncWorker（心跳 + 远程命令执行回报）**登录进主界面即自动开启**，
  「停止」按钮已移除——心跳在 agent 存活期间不可关停（总后台靠心跳判定在线，见 README；
  改连接/密钥/token 保存后自动重启对接生效；封禁/停用解封后点「启动对接」重连）
- 会员/操作员QQ 的重复管理入口已移除：会员群同步统一走「群管理/会员」页（本页不再重复提供）；
  操作员 = 本机登录并连总后台的 QQ（普通QQ即群会员，走会员同步），对接启动后自动随心跳
  登记/续活并记录最近登录时间/登录IP/登录位置，无需本页手动维护
- 封禁停摆：执行器被封禁（40310）或操作员被停用（40311）→ 对接线程停摆红字提示，解封后可重启
- 命令/事件日志

线程纪律：所有网络操作在临时 daemon 线程，UI 一律 after(0) 回主线程改控件
（与 main_window 其它 tab 同款模式）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable

import customtkinter as ctk

from integration.backend_client import BackendError, HbjfClient
from integration.sync_worker import SyncWorker

if False:  # pragma: no cover — 仅类型注释用
    from .config_manager import AppConfig


class BackendTab(ctk.CTkScrollableFrame):
    def __init__(self, master, cfg: "AppConfig", client, save_config: Callable,
                 app_version: str = ""):
        super().__init__(master)
        self.cfg = cfg
        self.client = client  # PluginClient（取群成员/同步插件配置）
        self.save_config = save_config
        self.app_version = app_version

        self.worker: SyncWorker | None = None
        self._payload_cache: dict[str, tuple[float, dict]] = {}
        self._login_cache: tuple[float, dict | None] | None = None  # 本机登录QQ信息缓存（心跳 admin_qq 用）

        self._build_ui()

    # ================= 界面构建 =================

    def _build_ui(self) -> None:
        # ---- 连接设置 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=(2, 6))
        ctk.CTkLabel(card, text="总后台连接", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))

        self.var_base = ctk.StringVar(value=self.cfg.backend_base)
        self.var_key = ctk.StringVar(value=self.cfg.backend_api_key)
        self.var_token = ctk.StringVar(value=self.cfg.executor_token)
        self.var_name = ctk.StringVar(value=self.cfg.executor_name)
        self.var_interval = ctk.StringVar(value=str(self.cfg.heartbeat_interval_sec))

        rows = [
            ("总后台地址", self.var_base, "http://localhost:8892"),
            ("对接密钥", self.var_key, "X-Api-Key"),
            ("执行器 token", self.var_token, "总后台新增执行器时生成"),
            ("执行器名称", self.var_name, "仅用于本机展示"),
            ("心跳间隔(秒)", self.var_interval, "最小 5"),
        ]
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=10, pady=2)
        for i, (label, var, ph) in enumerate(rows):
            ctk.CTkLabel(grid, text=label, width=96, anchor="w").grid(row=i, column=0, sticky="w", pady=1)
            ctk.CTkEntry(grid, textvariable=var, width=230, placeholder_text=ph).grid(
                row=i, column=1, sticky="we", padx=(4, 0), pady=1)
        grid.grid_columnconfigure(1, weight=1)

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(btns, text="测试连接", width=100, command=self._test_connection).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="保存设置", width=100, command=self._save_settings).pack(side="left", padx=6)
        self.lbl_hb = ctk.CTkLabel(btns, text="心跳：未启动", text_color="gray")
        self.lbl_hb.pack(side="left", padx=10)

        # ---- 执行器控制 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=6)
        ctk.CTkLabel(card, text="执行器运行", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkButton(row, text="启动对接（心跳+远程命令）", width=220, command=self._start_worker).pack(side="left", padx=(0, 6))
        self.lbl_state = ctk.CTkLabel(row, text="未启动", text_color="gray")
        self.lbl_state.pack(side="left", padx=10)
        ctk.CTkLabel(card, text="心跳随 agent 启动自动开启（无需手动操作，无停止入口）；"
                              "改连接/密钥/token 保存后自动重启对接生效；心跳间隔保存后下个周期生效。",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(anchor="w", padx=10, pady=(0, 2))

        self.lbl_sync = ctk.CTkLabel(card, text="会员同步：—（同步入口见「群管理/会员」页）",
                                     text_color="gray", wraplength=720, justify="left", anchor="w")
        self.lbl_sync.pack(fill="x", padx=10, pady=(0, 8))

        # ---- 日志 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="both", expand=True, padx=4, pady=6)
        ctk.CTkLabel(card, text="运行日志", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self.txt_log = ctk.CTkTextbox(card, height=130, state="disabled", font=ctk.CTkFont(size=11))
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 8))

    # ================= 连接与保存 =================

    def _client_from_entries(self) -> HbjfClient:
        return HbjfClient(self.var_base.get().strip(), self.var_key.get().strip(),
                          self.var_token.get().strip())

    def _test_connection(self) -> None:
        base = self.var_base.get().strip()
        if not base:
            self._log("请先填写总后台地址")
            return
        client = self._client_from_entries()

        def work() -> None:
            try:
                r = client.heartbeat(host="gui-test", version="test")
                self.after(0, lambda: self._set_hb(f"测试连接成功（心跳 {r.get('now', '')}）", "#2ecc71"))
                self.after(0, lambda: self._log("测试连接成功"))
            except BackendError as e:
                self.after(0, lambda e=e: self._set_hb(str(e), "#e74c3c"))

        threading.Thread(target=work, daemon=True).start()

    def _apply_to_cfg(self) -> str | None:
        """把界面值写回 cfg；非法返回错误信息。"""
        interval = self.var_interval.get().strip()
        try:
            interval = max(5, int(interval))
        except ValueError:
            return "心跳间隔必须是整数秒"
        self.cfg.backend_base = self.var_base.get().strip()
        self.cfg.backend_api_key = self.var_key.get().strip()
        self.cfg.executor_token = self.var_token.get().strip()
        self.cfg.executor_name = self.var_name.get().strip()
        self.cfg.heartbeat_interval_sec = interval
        return None

    def _save_settings(self) -> None:
        old = (self.cfg.backend_base, self.cfg.backend_api_key, self.cfg.executor_token)
        err = self._apply_to_cfg()
        if err:
            self._log(f"保存失败：{err}")
            return
        changed_conn = old != (self.cfg.backend_base, self.cfg.backend_api_key, self.cfg.executor_token)
        self.save_config(self.cfg)
        if changed_conn and self.worker and self.worker.is_alive():
            # 心跳没有手动停止入口 → 连接参数变更时自动重启对接，避免新旧设置并存
            self._log("连接设置已变更：自动重启对接使新设置生效")
            self.worker.stop_worker()
            self.worker = None
            self._start_worker()
            return
        if changed_conn:
            self._log("设置已保存（连接/密钥/token 已更新，点「启动对接」生效）")
        else:
            self._log("设置已保存")

    def _set_hb(self, text: str, color: str) -> None:
        self.lbl_hb.configure(text=text, text_color=color)

    # ================= 执行器 worker =================

    def _start_worker(self) -> None:
        if self.worker and self.worker.is_alive():
            self._log("对接线程已在运行")
            return
        token = self.var_token.get().strip()
        if not token:
            self._log("请先填写执行器 token（总后台「执行器管理」新增后生成）")
            return
        self._apply_to_cfg()
        self.save_config(self.cfg)
        client = self._client_from_entries()
        self.worker = SyncWorker(
            client=client,
            cfg=self.cfg,
            save_config=self.save_config,
            on_event=self._on_worker_event,
            members_provider=self._members_provider,
            group_provider=self._group_provider,
            plugin_config_sync=getattr(self.client, "sync_plugin_config", None),
            admin_qq_provider=self._admin_qq_provider,
            version=self.app_version or "dev",
        )
        self.worker.start_worker()
        self.lbl_state.configure(text="运行中", text_color="#2ecc71")
        self._log(f"对接线程已启动（{client.base_url}，间隔 {self._interval_value()}s）")

    def _interval_value(self) -> int:
        try:
            return max(5, int(self.var_interval.get()))
        except ValueError:
            return 10

    def _stop_worker(self) -> None:
        """停止对接线程（仅供关窗/自动重启内部调用，界面无「停止」入口）。"""
        if self.worker:
            self.worker.stop_worker()
            self.worker = None
        self.lbl_state.configure(text="已停止", text_color="gray")
        self._set_hb("心跳：未启动", "gray")
        self._log("对接线程已停止")

    def auto_start(self) -> None:
        """登录进主界面后由 MainWindow 调用：配置齐全即自动开启心跳与远程命令。

        心跳没有停止入口，agent 存活期间持续上报 → 总后台在线状态与「agent 能调用
        总后台」不再矛盾（停止心跳按钮已移除）。缺配置只提示一次，不重复打扰。"""
        if self.worker and self.worker.is_alive():
            return
        if not (self.cfg.backend_base and self.cfg.backend_api_key and self.cfg.executor_token):
            self.lbl_state.configure(text="未自动启动（缺连接/token 配置）", text_color="gray")
            self._log("尚未配置完整的总后台连接与执行器 token——未自动开启心跳；"
                      "在上方填写并保存后点「启动对接」")
            return
        self._start_worker()
        self._log("agent 启动：已按保存的配置自动开启心跳与远程命令（总后台将实时显示在线）")

    def _on_worker_event(self, kind: str, payload) -> None:
        try:
            self.after(0, lambda: self._apply_event(kind, payload))
        except Exception:
            pass  # 窗口销毁竞态时忽略

    def _apply_event(self, kind: str, payload) -> None:
        if kind == "heartbeat":
            if payload == "ok":
                self._set_hb(f"在线（心跳 {datetime.now().strftime('%H:%M:%S')}）", "#2ecc71")
            else:
                self._set_hb(f"心跳失败：{payload}", "#e74c3c")
        elif kind == "poll_error":
            self._log(f"命令拉取失败：{payload}")
        elif kind == "command":
            p = payload or {}
            extra = ""
            if p.get("report_error"):
                extra = f"（回报失败：{p['report_error']}）"
            self._log(f"[命令 #{p.get('id')}] {p.get('command')} → {p.get('status')}: {p.get('message')}{extra}")
        elif kind == "sync":
            p = payload or {}
            warn = p.get("warning") or ""
            msg = (f"会员同步完成：成员 {p.get('total')}，新增 {p.get('added')}，"
                   f"已存在 {p.get('existed')}，跳过 {p.get('skipped')}"
                   + (f"；注意：{warn}" if warn else ""))
            self.lbl_sync.configure(text=msg, text_color="#e67e22" if warn else "#2ecc71")
            self._log(msg)
        elif kind == "sync_progress":
            self.lbl_sync.configure(text=f"同步中… {payload}", text_color="#3498db")
        elif kind == "fatal":
            self.lbl_state.configure(text="异常退出", text_color="#e74c3c")
            self._log(f"对接线程异常：{payload}")
        elif kind == "banned":
            self._mark_banned(payload)

    def _mark_banned(self, reason) -> None:
        """执行器被封禁/操作员被停用 → 停摆红字（须在 UI 线程调用）。"""
        self.lbl_state.configure(text="已封禁/停用（停摆）", text_color="#e74c3c")
        self._set_hb("心跳已停（停摆）", "#e74c3c")
        self.lbl_sync.configure(text=f"停摆：{reason}", text_color="#e74c3c")
        self._log(f"执行器停摆：{reason}。需总后台解封/停用解除或更换 token 后重新「启动对接」")

    # ================= 成员数据提供者（心跳/远程命令同步会员用） =================

    def _fetch_payload(self, group_id: str) -> dict:
        """取群成员全量 payload（60s 缓存，避免一次同步重复拉取）。"""
        key = str(group_id)
        hit = self._payload_cache.get(key)
        now = time.time()
        if hit and now - hit[0] < 60:
            return hit[1]
        try:
            payload = self.client.get_group_members(key, no_cache=True) or {}
        except Exception as e:  # noqa: BLE001 — 上报给调用方统一处理
            raise RuntimeError(f"拉取群 {key} 成员失败：{e}") from e
        self._payload_cache[key] = (now, payload)
        return payload

    def _members_provider(self, group_id: str) -> list[dict]:
        payload = self._fetch_payload(group_id)
        return (payload or {}).get("members") or []

    def _group_provider(self, group_id: str) -> dict:
        payload = self._fetch_payload(group_id)
        return payload or {}

    # ================= 登录信息（本机 QQ，worker 心跳 admin_qq 用） =================

    def _login_info(self) -> dict | None:
        """取本机登录QQ信息（含 qq/uin/nickname/nick 冗余键，60s 缓存）。
        来源：NapCat WebUI get_login_info；取不到回退插件状态 self_uin；再取不到返回 None。"""
        now = time.time()
        if self._login_cache and now - self._login_cache[0] < 60:
            return self._login_cache[1]
        info = {}
        try:
            from .napcat_paths import find_napcat_dir
            from .napcat_webui import NapCatWebUI

            napcat = find_napcat_dir(self.cfg)
            if napcat:
                webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
                if webui.ping():
                    info = webui.get_login_info() or {}
        except Exception:  # noqa: BLE001 — 找不到目录/未登录等静默降级
            info = {}
        if not info:
            try:
                st = self.client.get_status()
                uin = getattr(st, "self_uin", None)
                if uin is None and isinstance(st, dict):
                    uin = st.get("self_uin")
                if uin:
                    info = {"uin": uin}
            except Exception:  # noqa: BLE001
                pass
        qq = str(info.get("uin") or info.get("user_id") or "").strip()
        result = None
        if qq.isdigit():
            nick = str(info.get("nick") or info.get("nickname") or "").strip()
            result = {"qq": qq, "uin": qq, "nickname": nick, "nick": nick}
        self._login_cache = (now, result)
        return result

    def _admin_qq_provider(self) -> dict | None:
        """SyncWorker 心跳用的本机管理员QQ（worker 线程调用，失败静默返回 None）。"""
        return self._login_info()

    # ================= 日志 / 关闭 =================

    def _log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n"
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line)
        if int(self.txt_log.index("end-1c").split(".")[0]) > 500:
            self.txt_log.delete("1.0", "100.0")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def shutdown(self) -> None:
        """窗口关闭时调用：停掉对接线程，确保无残留。"""
        if self.worker:
            self.worker.stop_worker()
            self.worker = None
