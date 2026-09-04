"""第 8 个 Tab「游戏玩法」：多游戏群玩法回复 + 结算自动上报总后台。

能力（v2）：
- 从总后台拉取启用的玩法列表，下载所选玩法 .py 到本地缓存
- 游戏群：可填多个（逗号分隔）。插件把已启用群的群消息转发到本机回调端口 →
  玩法引擎按规则算 (回复, 积分变动 delta) → 有回复时插件自动 @ 发言者发回群里
- 玩法协议 v2：handle_message(group_id, qq, nickname, text) 可返回
  None / 回复文本 / (回复, delta) / {"reply":…, "delta":…}（str 单返回=旧协议 delta=0）
- 每个游戏群维护「当前局」（RoundSession）：有回复或 delta≠0 的消息入局记回放；
  达到 1000 事件自动结算上报一次；「结算并上报」手动上报全部群（round_id 幂等，
  可重复结算不重复入账）；上报失败事件保留可重试
- 启用玩法时可自动把游戏群成员注册为会员并拉积分（play_auto_register_members 开关）
- 执行器被封禁（40310）/操作员停用（40311）→ 玩法停摆红字，事件保留待解封后重试上报
- 重启 GUI 自动恢复上次启用的玩法（重新下载最新版 + 多群转发）

线程纪律：所有网络/引擎操作在临时 daemon 线程，UI 一律 after(0) 回主线程改控件
（与 backend_tab / main_window 同款模式）。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import customtkinter as ctk
import requests

from integration.backend_client import BackendError, ExecutorBanned, HbjfClient, OperatorDisabled
from integration.member_sync import group_create_time_str, run_member_sync
from integration.play_engine import FLUSH_LIMIT, HARD_LIMIT, RoundSession, RuleEngine

if False:  # pragma: no cover — 仅类型注释用
    from .config_manager import AppConfig


class PlayTab(ctk.CTkScrollableFrame):
    def __init__(self, master, cfg: "AppConfig", client, save_config: Callable,
                 app_version: str = ""):
        super().__init__(master)
        self.cfg = cfg
        self.client = client  # PluginClient（群成员拉取 / 插件 API 地址）
        self.save_config = save_config
        self.app_version = app_version

        self.engine = RuleEngine(rules_dir=cfg.plays_dir())
        self.session: RoundSession | None = None  # 启用玩法后存在；停止后清空
        self._active_groups: set[str] = set()     # 已启用转发的游戏群
        self._msg_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._server_error = ""
        self._rules_rows: list[dict] = []
        self._selected_rule_id: int | None = None
        self._busy = False
        self._banned_reason: str = ""               # 非空=玩法已停摆（红字展示）
        self._last_settle: dict[str, dict] = {}     # gid -> {ok, error, time}
        self._payload_cache: dict[str, tuple[float, dict]] = {}  # 群成员 payload 30s 缓存

        self._build_ui()
        self._start_callback_server()
        # GUI 启动时自动恢复上次启用的玩法
        self.after(400, self._auto_restore)

    # ================= 界面构建 =================

    def _build_ui(self) -> None:
        # ---- 引擎状态 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=(2, 6))
        ctk.CTkLabel(card, text="玩法回复引擎（游戏群多群，回复自动@发言者）",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 2))
        self.lbl_state = ctk.CTkLabel(card, text="初始化中…", text_color="orange", wraplength=760,
                                      justify="left", anchor="w")
        self.lbl_state.pack(fill="x", padx=10, pady=(2, 4))

        # ---- 玩法选择（总后台）----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=6)
        ctk.CTkLabel(card, text="玩法选择（总后台「会员玩法管理」上传，仅启用状态可见）",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=2)
        self.opt_rule = ctk.CTkOptionMenu(row, values=["（先刷新玩法列表）"], width=300,
                                          command=self._on_rule_picked)
        self.opt_rule.pack(side="left")
        ctk.CTkButton(row, text="刷新玩法列表", width=120, command=self._refresh_rules).pack(side="left", padx=6)
        self.lbl_rules = ctk.CTkLabel(row, text="未连接", text_color="gray")
        self.lbl_rules.pack(side="left", padx=8)

        # ---- 游戏群勾选（多群，运行中可随时增删）----
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(row, text="游戏群（勾选参与玩法的群，可多选）", width=230, anchor="w").pack(side="left")
        ctk.CTkButton(row, text="刷新群列表", width=100, command=self._refresh_group_checkboxes).pack(side="left", padx=4)
        self.var_auto_members = ctk.BooleanVar(value=self.cfg.play_auto_register_members)
        ctk.CTkCheckBox(row, text="启用时自动注册群成员为会员", variable=self.var_auto_members).pack(side="left", padx=8)
        ctk.CTkLabel(row, text="（群号在「群管理」页维护，运行中勾选/取消即时生效）",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left")

        self.frame_group_list = ctk.CTkScrollableFrame(card, height=120)
        self.frame_group_list.pack(fill="x", padx=10, pady=(4, 2))
        self._group_vars: dict[str, ctk.BooleanVar] = {}
        self._group_labels: dict[str, ctk.CTkLabel] = {}

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkButton(row, text="启用玩法", width=100, command=self._enable_play).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="停止玩法（先结算）", width=140, command=self._stop_play).pack(side="left", padx=6)
        ctk.CTkButton(row, text="结算并上报", width=110, command=lambda: self._settle_groups(failed_only=False)).pack(side="left", padx=6)
        ctk.CTkButton(row, text="重试未上报", width=110, command=lambda: self._settle_groups(failed_only=True)).pack(side="left", padx=6)
        ctk.CTkLabel(row, text="玩法=Python 文件：handle_message(群号,QQ,昵称,发言) → 回复文本 / None，"
                              "或 (回复,积分变动) / {reply,delta} 返回积分；结算上报总后台校验后入账（round_id 幂等）",
                     text_color="gray", font=ctk.CTkFont(size=11), wraplength=430, justify="left").pack(
            side="left", padx=8)

        # ---- 各群当前局状态 ----
        self.lbl_rounds = ctk.CTkLabel(self, text="", text_color="#2ecc71", anchor="w", justify="left",
                                       wraplength=900, font=ctk.CTkFont(size=12))
        self.lbl_rounds.pack(fill="x", padx=14, pady=(0, 4))

        # ---- 日志 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="both", expand=True, padx=4, pady=6)
        ctk.CTkLabel(card, text="玩法日志（收到的发言 / 入局计分 / 结算上报结果）",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self.txt_log = ctk.CTkTextbox(card, height=170, state="disabled", font=ctk.CTkFont(size=11))
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # 所有控件就绪后再重建群勾选列表（内部会刷新各群状态标签）
        self._refresh_group_checkboxes()

    # ================= 回调服务（插件 → 本页） =================

    def _start_callback_server(self) -> None:
        """常驻 127.0.0.1:<port>/play/msg 接收插件转发的群消息（未启用玩法/未启用的群直接忽略）。"""
        port = int(self.cfg.play_callback_port or 6101)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                app = self.server.app  # type: ignore[attr-defined]
                try:
                    if self.path.rstrip("/") != "/play/msg":
                        self.send_response(404)
                        self.end_headers()
                        return
                    length = int(self.headers.get("content-length") or 0)
                    raw = self.rfile.read(length) if length else b""
                    data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                    app._on_callback_message(data)
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"code":0,"message":"ok"}')
                except Exception:
                    try:
                        self.send_response(500)
                        self.end_headers()
                    except Exception:
                        pass

            def log_message(self, *args: Any) -> None:  # 静默访问日志
                pass

        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            self._server.app = self  # type: ignore[attr-defined]
            self._server_thread = threading.Thread(target=self._server.serve_forever,
                                                   name="play-http", daemon=True)
            self._server_thread.start()
            self._server_error = ""
        except OSError as e:
            self._server_error = f"本地回调端口 {port} 被占用，玩法消息收不到: {e}"
            self._log(f"✗ {self._server_error}")

        self._worker = threading.Thread(target=self._worker_loop, name="play-worker", daemon=True)
        self._worker.start()
        self._apply_state()

    def _on_callback_message(self, data: dict) -> None:
        """HTTP 线程：校验后入队（不碰 UI/引擎）。"""
        group_id = str(data.get("group_id") or "")
        qq = str(data.get("qq") or "")
        text = str(data.get("text") or "")
        if not group_id or not qq or not text:
            return
        self._msg_queue.put({"group_id": group_id, "qq": qq,
                             "nickname": str(data.get("nickname") or ""), "text": text})

    def _worker_loop(self) -> None:
        """消息 worker：过玩法引擎 → 入局记回放 → 需要回复时发回群里；事件满 soft 上限自动结算。"""
        while not self._stop.is_set():
            try:
                item = self._msg_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle_one(item)
            except Exception as e:  # noqa: BLE001 — 引擎链路任何异常都不能停 worker
                self.after(0, self._log, f"✗ 消息处理异常: {e}")

    def _handle_one(self, item: dict) -> None:
        group_id = item["group_id"]
        qq = item["qq"]
        nickname = item.get("nickname") or ""
        text = item["text"]
        who = f"{nickname}({qq})" if nickname else f"QQ{qq}"

        # 停摆/未启用或该群不在启用列表 → 直接忽略（引擎已 deactivate 时 handle 恒 None）
        if not self.engine.is_active() or group_id not in self._active_groups:
            return

        reply = self.engine.handle_message(group_id, qq, nickname, text)
        delta = self.engine.last_delta if hasattr(self.engine, "last_delta") else 0
        if self.engine.last_error:
            self.after(0, self._log, f"✗ {self.engine.last_error}")
        reply = reply.strip() if reply else ""

        # 入局条件：有回复（引擎认为值得说）或产生积分变动；白说且无变动不记录
        if (reply or delta != 0) and self.session is not None:
            info = self.session.add_event(group_id, qq, nickname, text, reply, delta)
            if info.get("dropped"):
                self.after(0, self._log,
                           f"⚠ 群{group_id} 单局事件已达上限 {HARD_LIMIT}，本条未入局，"
                           f"请尽快「结算并上报」")
            else:
                self.after(0, self._rounds_refresh)
                if int(info.get("event_count") or 0) >= FLUSH_LIMIT:
                    self._auto_settle(group_id)  # soft 上限：worker 线程内自动结算一次

        if reply:
            self.after(0, self._log,
                       f"收到 群{group_id} {who}: {text[:80]}"
                       + (f"（入局 {delta:+d}）" if delta else ""))
            self._send_reply(group_id, qq, nickname, who, reply, delta)
        elif delta:
            self.after(0, self._log,
                       f"收到 群{group_id} {who}: {text[:80]}（入局 {delta:+d}，无回复）")

    def _send_reply(self, group_id: str, qq: str, nickname: str, who: str,
                    reply: str, delta: int) -> None:
        url = self.cfg.plugin_api("play/reply")
        try:
            r = requests.post(url, json={"group_id": group_id, "at_qq": qq, "text": reply},
                              timeout=5)
            if r.ok:
                self.after(0, self._log,
                           f"→ 已@ {who} 回复: {reply[:80]}")
            else:
                self.after(0, self._log,
                           f"✗ 回复发送失败 HTTP {r.status_code}: {r.text[:120]}")
        except requests.RequestException as e:
            self.after(0, self._log, f"✗ 回复发送失败（插件不可达）: {e}")

    def _auto_settle(self, group_id: str) -> None:
        """事件数到 soft 上限时自动结算（worker 线程调用；失败事件保留，下轮/手动可重试）。"""
        try:
            res = self.session.settle(group_id)
        except (ExecutorBanned, OperatorDisabled) as e:
            self.after(0, lambda e=e: self._mark_banned(e))
            return
        except BackendError as e:
            self.after(0, self._log,
                       f"⚠ 群{group_id} 自动结算失败（事件保留，稍后「重试未上报」）: {e}")
            return
        self.after(0, self._settle_result_ui, res)

    # ================= 玩法启停 / 结算 =================

    def _client(self) -> HbjfClient:
        """总后台客户端（带执行器 token：群/会员/对局上报都据此校验执行器）。"""
        return HbjfClient(self.cfg.backend_base, self.cfg.backend_api_key,
                          token=self.cfg.executor_token)

    def _refresh_group_checkboxes(self) -> None:
        """重建群勾选列表（来源：本机监控群 ∪ 上次玩法群；上次玩法群默认勾选）。"""
        for w in self.frame_group_list.winfo_children():
            w.destroy()
        self._group_vars = {}
        self._group_labels = {}
        groups: list[str] = []
        for g in self.cfg.watch_group_list():
            if g not in groups:
                groups.append(g)
        for g in self.cfg.play_group_list():
            if g not in groups:
                groups.append(g)
        checked = set(self.cfg.play_group_list()) or set(self.cfg.watch_group_list())
        for g in groups:
            var = ctk.BooleanVar(value=g in checked)
            row = ctk.CTkFrame(self.frame_group_list, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkCheckBox(row, text=g, width=130, variable=var,
                            command=lambda g=g: self._on_group_toggled(g)).pack(side="left")
            self._group_vars[g] = var
            lbl = ctk.CTkLabel(row, text="", text_color="gray", anchor="w", font=ctk.CTkFont(size=11))
            lbl.pack(side="left", padx=8)
            self._group_labels[g] = lbl
        if not groups:
            ctk.CTkLabel(self.frame_group_list,
                         text="（暂无群：请到「群管理」页添加，或到「实时监控」填写监控群）",
                         text_color="gray").pack(anchor="w", padx=4, pady=4)
        self._rounds_refresh()

    def _checked_groups(self) -> list[str]:
        return [g for g, v in self._group_vars.items() if v.get()]

    def _push_play_groups(self) -> None:
        """把当前激活的游戏群全量推给插件转发（运行中增减群后调用）。"""
        try:
            callback = f"http://127.0.0.1:{int(self.cfg.play_callback_port or 6101)}/play/msg"
            requests.post(self.cfg.plugin_api("play/config"),
                          json={"enabled": True, "groups": sorted(self._active_groups),
                                "callback": callback}, timeout=5)
        except requests.RequestException:
            pass

    def _on_group_toggled(self, gid: str) -> None:
        """勾选/取消（运行中即时生效：加群或先结算再退群）。"""
        var = self._group_vars.get(gid)
        if var is None:
            return
        if not self.engine.is_active():
            return
        if var.get():
            self._active_groups.add(gid)
            self._push_play_groups()
            self._log(f"已加入游戏群 {gid}")
        else:
            # 退群前先结算该群未上报对局；失败则恢复勾选并提示
            if self.session and gid in self.session.pending_groups():
                try:
                    self.session.settle(gid)
                except (ExecutorBanned, OperatorDisabled) as e:
                    var.set(True)
                    self._mark_banned(e)
                    return
                except BackendError as e:
                    var.set(True)
                    self._log(f"⚠ 群{gid} 有未结算对局且结算失败（已保留勾选）: {e}")
                    return
            self._active_groups.discard(gid)
            self._push_play_groups()
            self._log(f"已退出游戏群 {gid}")
        self._apply_state()
        self._rounds_refresh()

    def _refresh_rules(self) -> None:
        self.after(0, self._set_busy, True)
        self.after(0, self._log, f"刷新玩法列表 ← {self.cfg.backend_base} …")

        def run() -> None:
            try:
                rows = self._client().list_rules()
            except BackendError as e:
                self.after(0, self._log, f"✗ 拉取玩法列表失败: {e}")
                self.after(0, self._set_busy, False)
                return
            self._rules_rows = rows or []
            displays = [f"{r.get('name') or ('玩法#' + str(r.get('id')))}"
                        f"@{r.get('version') or '?'}　(#{r.get('id')})" for r in self._rules_rows]
            self.after(0, self._apply_rules_ui, displays)

        threading.Thread(target=run, name="play-refresh", daemon=True).start()

    def _apply_rules_ui(self, displays: list[str]) -> None:
        self.opt_rule.configure(values=displays or ["（无启用的玩法，去总后台玩法页上传）"])
        if displays:
            self.opt_rule.set(displays[0])
            self._selected_rule_id = int(self._rules_rows[0]["id"])
        else:
            self.opt_rule.set("（无启用的玩法，去总后台玩法页上传）")
            self._selected_rule_id = None
        self.lbl_rules.configure(text=f"共 {len(self._rules_rows)} 个启用玩法")
        self._set_busy(False)

    def _on_rule_picked(self, choice: str) -> None:
        if choice.startswith("（"):
            self._selected_rule_id = None
            return
        for r in self._rules_rows:
            if str(r.get("id")) in choice and (r.get("name") or "") in choice:
                self._selected_rule_id = int(r["id"])
                return
        # 兜底：按尾部的 (#id)
        try:
            self._selected_rule_id = int(choice.rsplit("#", 1)[1].rstrip("）").strip())
        except (ValueError, IndexError):
            self._selected_rule_id = None

    def _selected_rule(self) -> dict | None:
        for r in self._rules_rows:
            if r.get("id") == self._selected_rule_id:
                return r
        return None

    def _backend_banned_groups(self, client: HbjfClient) -> set[str]:
        """总后台里 status=banned 的群（封禁群停玩：不允许再启用该群玩法）。"""
        try:
            rows = client.list_groups()
        except BackendError:
            return set()  # 拉不到群列表不阻塞（首次对接/无群库时允许）
        return {str(g.get("group_id")) for g in rows if str(g.get("status")) == "banned"}

    def _enable_play(self) -> None:
        if self._busy:
            self._log("上一个操作还没完成，请稍候")
            return
        rule = self._selected_rule()
        if rule is None:
            self._log("✗ 请先「刷新玩法列表」并选中一个玩法")
            return
        groups = self._checked_groups()
        if not groups:
            self._log("✗ 请先勾选游戏群（群号在「群管理」页维护）")
            return
        if self.engine.is_active():
            self._log("✗ 已有玩法在运行：请先「停止玩法（先结算）」")
            return
        if self.session and self.session.pending_count() and not self.engine.is_active():
            # 上次停摆残留的未上报局：必须先处理，避免换玩法丢局
            self._log("✗ 还有上次未上报的对局（执行器被封禁/停用时留下），请先「重试未上报」")
            return
        rule_id = int(rule["id"])
        rule_name = str(rule.get("name") or f"玩法#{rule_id}")
        version = str(rule.get("version") or "?")
        auto_members = bool(self.var_auto_members.get())
        self.cfg.play_auto_register_members = auto_members
        self._set_busy(True)
        self._banned_reason = ""
        self._log(f"启用玩法 {rule_name}@{version}，游戏群 {','.join(groups)} …")

        def run() -> None:
            client = self._client()
            banned = self._backend_banned_groups(client)
            if banned & set(groups):
                self.after(0, self._log,
                           f"✗ 游戏群 {','.join(sorted(banned & set(groups)))} 已被总后台封禁"
                           f"（封禁群停玩），本次启用取消")
                self.after(0, self._set_busy, False)
                return
            try:
                # 1) 下载玩法文件（覆盖 = 总后台重传后自动取最新）
                dest = self.cfg.plays_dir() / f"rule_{rule_id}.py"
                client.download_rule(rule_id, str(dest))
            except BackendError as e:
                self.after(0, self._log, f"✗ 下载玩法失败: {e}")
                self.after(0, self._set_busy, False)
                return
            # 2) 打开插件转发（失败则中止，避免本地跑了但收不到消息）
            callback = f"http://127.0.0.1:{int(self.cfg.play_callback_port or 6101)}/play/msg"
            try:
                r = requests.post(self.cfg.plugin_api("play/config"),
                                  json={"enabled": True, "groups": groups,
                                        "callback": callback}, timeout=5)
                r.raise_for_status()
            except requests.RequestException as e:
                self.after(0, self._log, f"✗ 通知插件转发失败（NapCat 插件未就绪?）: {e}")
                self.after(0, self._set_busy, False)
                return
            # 3) 本地引擎激活
            err = self.engine.activate(rule_id, f"{rule_name}@{version}", str(dest))
            if err:
                self.after(0, self._log, f"✗ 玩法加载失败，已停用: {err}")
                try:
                    requests.post(self.cfg.plugin_api("play/config"),
                                  json={"enabled": False}, timeout=3)
                except requests.RequestException:
                    pass
                self.after(0, self._set_busy, False)
                return
            # 4) 建对局会话（各群独立 round_id，结算时带 token 上报总后台）
            self.session = RoundSession(client=self._client(), play_name=rule_name, play_id=rule_id)
            self._active_groups = set(groups)
            # 5) 记录配置（重启自动恢复）
            self.cfg.play_rule_id = rule_id
            self.cfg.play_rule_name = f"{rule_name}@{version}"
            self.cfg.play_group_id = ",".join(groups)
            self.cfg.play_enabled = True
            self.save_config(self.cfg)
            self.after(0, self._log,
                       f"✓ 玩法已启用：{self.cfg.play_rule_name} ｜ 游戏群 {','.join(groups)}"
                       f"（有回复或积分变动的发言自动入局记回放，机器人自己发言不会触发）")
            # 6) 可选：把各游戏群成员注册为会员（幂等；已有会员只拉积分不动余额）
            if auto_members:
                for gid in groups:
                    if not self._sync_members_quiet(client, gid):
                        break  # 封禁异常已内部处理并停摆
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-enable", daemon=True).start()

    def _sync_members_quiet(self, client: HbjfClient, gid: str) -> bool:
        """把一个游戏群成员同步为会员（幂等）。停摆异常 → 上抛给 _mark_banned 处理，返回 False。"""
        def log(msg: str) -> None:
            self.after(0, self._log, msg)

        log(f"注册群 {gid} 成员为会员 …")
        try:
            result = run_member_sync(client, gid, self._members_provider,
                                     self._group_provider, on_progress=log)
            warn = result.get("warning") or ""
            log(f"群 {gid} 会员同步：成员 {result.get('total')}，新增 {result.get('added')}，"
                f"已存在 {result.get('existed')}" + (f"；注意：{warn}" if warn else ""))
            return True
        except (ExecutorBanned, OperatorDisabled) as e:
            self._mark_banned(e)
            return False
        except BackendError as e:
            log(f"⚠ 群 {gid} 会员同步失败（不阻断玩法）: {e}")
            return True

    def _settle_groups(self, failed_only: bool) -> None:
        """结算并上报 / 重试未上报（后台线程逐群结算，单个失败不影响其它群）。"""
        if self._busy:
            self._log("上一个操作还没完成，请稍候")
            return
        if self.session is None or not self.session.pending_count():
            self._log("当前没有待结算的对局" if not failed_only else "当前没有未上报的对局")
            return
        pending = self.session.pending_groups()
        if failed_only:
            pending = [g for g in pending if not self._last_settle.get(g, {}).get("ok")]
            if not pending:
                self._log("没有失败待重试的对局")
                return
        self._set_busy(True)
        self._log(("结算并上报 " if not failed_only else "重试未上报 ") +
                  f"{len(pending)} 个群的对局 …")

        def run() -> None:
            session = self.session
            session.client = self._client()  # 结算时刻最新 token（防止后台换了 token 后旧值一直失败）
            banned_hit = False
            for gid in pending:
                if banned_hit:
                    break  # 已停摆：其余群的结算必然同错，事件全部保留待解封
                try:
                    res = session.settle(gid)
                except (ExecutorBanned, OperatorDisabled) as e:
                    self._mark_banned(e)
                    banned_hit = True
                    continue
                except BackendError as e:
                    self._last_settle[gid] = {"ok": False, "error": str(e),
                                              "time": datetime.now().strftime("%H:%M:%S")}
                    self.after(0, self._log,
                               f"✗ 群{gid} 结算失败（事件保留，可「重试未上报」）: {e}")
                    continue
                self._last_settle[gid] = {"ok": not res.get("empty") and res.get("server") is not None,
                                          "error": "" if res.get("server") is not None else "empty",
                                          "time": datetime.now().strftime("%H:%M:%S")}
                self.after(0, self._settle_result_ui, res)
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-settle", daemon=True).start()

    def _settle_result_ui(self, res: dict) -> None:
        """结算单群结果的 UI 回显（须在 UI 线程）。res 来自 RoundSession.settle。"""
        if res.get("empty"):
            return
        if res.get("busy"):
            self._log("（该局正在上报中，忽略本次）")
            return
        gid = str(res.get("group_id") or "")
        server = res.get("server") or {}
        label = res.get("label") or "?"
        if not server:
            self._log(f"✗ 群{gid} 局@{label} 结算结果缺失")
            return
        self._last_settle[gid] = {"ok": True, "error": "", "time": datetime.now().strftime("%H:%M:%S")}
        warn = server.get("warning") or ""
        line = (f"✓ 群{gid} 局@{label}（{res.get('round_id', '')}）已上报："
                f"事件 {server.get('event_count')} ｜ 净变动 {server.get('total_delta'):+d}"
                f" ｜ 入账会员 {server.get('member_count')} 人"
                f" ｜ 服务器警告 {server.get('warning_count')} 条"
                f"{'（重复上报，未重复入账）' if server.get('duplicate') else ''}")
        self._log(line + (f"；详情: {warn}" if warn else ""))
        self._rounds_refresh()

    def _stop_play(self) -> None:
        """停止玩法：先结算未上报局（成功才清局），再关插件转发/引擎/配置。"""
        if self._busy:
            self._log("上一个操作还没完成，请稍候")
            return
        if not self.engine.is_active() and not (self.session and self.session.pending_count()):
            self._log("玩法未在运行")
            self._apply_state()
            return
        self._set_busy(True)
        self._log("停止玩法：先结算未上报的对局 …")

        def run() -> None:
            failed = False
            if self.session:
                self.session.client = self._client()
                for gid in self.session.pending_groups():
                    try:
                        res = self.session.settle(gid)
                        if res.get("server") is not None:
                            self.after(0, self._settle_result_ui, res)
                    except (ExecutorBanned, OperatorDisabled) as e:
                        self._mark_banned(e)  # 事件保留在 session，解封后「重试未上报」
                        self.after(0, self._set_busy, False)
                        self.after(0, self._apply_state)
                        return
                    except BackendError as e:
                        failed = True
                        self._last_settle[gid] = {"ok": False, "error": str(e),
                                                  "time": datetime.now().strftime("%H:%M:%S")}
                        self.after(0, self._log,
                                   f"✗ 群{gid} 停止前结算失败（事件保留，可「重试未上报」）: {e}")
                        break
            if failed:
                # 结算未成功就停 = 丢局：取消停止，保持玩法运行，网络恢复后重试或再停
                self.after(0, self._log,
                           "停止取消：请先把未上报对局结算成功（可「重试未上报」），再点「停止玩法」")
                self.after(0, self._apply_state)
                self.after(0, self._set_busy, False)
                return
            self.engine.deactivate()
            self.session = None
            self._active_groups = set()
            self._banned_reason = ""
            self._last_settle = {}
            try:
                requests.post(self.cfg.plugin_api("play/config"), json={"enabled": False}, timeout=3)
            except requests.RequestException as e:
                self._log(f"（插件转发关闭失败，可忽略）: {e}")
            self.cfg.play_enabled = False
            self.cfg.play_rule_id = 0
            self.cfg.play_rule_name = ""
            self.cfg.play_group_id = ""
            self.save_config(self.cfg)
            self.after(0, self._log, "✓ 玩法已停止")
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-stop", daemon=True).start()

    def _mark_banned(self, reason) -> None:
        """执行器被封禁/操作员被停用 → 玩法停摆：引擎停、插件转发关、事件保留待重试。
        可能从 worker/结算/同步线程调用，内部转 UI 线程。"""
        def on_ui() -> None:
            if self.engine.is_active():
                self.engine.deactivate()
            try:
                requests.post(self.cfg.plugin_api("play/config"), json={"enabled": False}, timeout=3)
            except requests.RequestException:
                pass
            if not self.cfg.play_enabled:
                pass  # 已处于停止态不重复保存
            else:
                self.cfg.play_enabled = False
                self.save_config(self.cfg)
            self._banned_reason = str(reason)
            self._active_groups = set()
            self._log(f"✗ 玩法停摆：{reason}。未上报对局已保留在本地，"
                      f"待总后台解封/停用解除后「重试未上报」或重新启用玩法")
            self._apply_state()

        try:
            self.after(0, on_ui)
        except Exception:
            pass  # 关窗竞态时忽略

    def _auto_restore(self) -> None:
        """GUI 启动后自动恢复上次启用的玩法（多群）。"""
        groups = self.cfg.play_group_list()
        if not (self.cfg.play_enabled and self.cfg.play_rule_id and groups):
            self._apply_state()
            return
        self._log(f"自动恢复上次玩法：{self.cfg.play_rule_name or ('#' + str(self.cfg.play_rule_id))}"
                  f" ｜ 游戏群 {','.join(groups)}")

        def run() -> None:
            rule_id = int(self.cfg.play_rule_id)
            rule_name = self.cfg.play_rule_name or f"玩法#{rule_id}"
            client = self._client()
            banned = self._backend_banned_groups(client)
            if banned & set(groups):
                self.after(0, self._log,
                           f"✗ 自动恢复取消：群 {','.join(sorted(banned & set(groups)))} 已被总后台封禁")
                self.after(0, self._apply_state)
                return
            try:
                dest = self.cfg.plays_dir() / f"rule_{rule_id}.py"
                client.download_rule(rule_id, str(dest))
            except BackendError as e:
                self.after(0, self._log, f"✗ 自动恢复失败（下载玩法）: {e}")
                self.after(0, self._apply_state)
                return
            callback = f"http://127.0.0.1:{int(self.cfg.play_callback_port or 6101)}/play/msg"
            try:
                requests.post(self.cfg.plugin_api("play/config"),
                              json={"enabled": True, "groups": groups,
                                    "callback": callback}, timeout=5)
            except requests.RequestException as e:
                self.after(0, self._log, f"✗ 自动恢复失败（插件转发）: {e}")
                self.after(0, self._apply_state)
                return
            err = self.engine.activate(rule_id, rule_name, str(dest))
            if err:
                self.after(0, self._log, f"✗ 自动恢复失败（玩法加载）: {err}")
            else:
                name = str(rule_name)
                self.session = RoundSession(client=self._client(), play_name=name, play_id=rule_id)
                self._active_groups = set(groups)
                self.after(0, self._log,
                           f"✓ 已恢复：{self.cfg.play_rule_name} ｜ 游戏群 {','.join(groups)}")
                if self.cfg.play_auto_register_members:
                    for gid in groups:
                        if not self._sync_members_quiet(client, gid):
                            break
            self.after(0, self._refresh_group_checkboxes)
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)

        threading.Thread(target=run, name="play-restore", daemon=True).start()

    # ================= 群成员 provider（会员同步用） =================

    def _fetch_payload(self, group_id: str) -> dict:
        """取插件群成员 payload（30s 缓存）。失败抛 RuntimeError 由同步方统一提示。"""
        key = str(group_id)
        hit = self._payload_cache.get(key)
        now = time.time()
        if hit and now - hit[0] < 30:
            return hit[1]
        payload = self.client.get_group_members(key, no_cache=True) or {}
        self._payload_cache[key] = (now, payload)
        return payload

    def _members_provider(self, group_id: str) -> list[dict]:
        return self._fetch_payload(group_id).get("members") or []

    def _group_provider(self, group_id: str) -> dict:
        return self._fetch_payload(group_id)

    # ================= 状态 / 日志 =================

    def _rounds_refresh(self) -> None:
        """各游戏群当前局状态行（UI 线程；有局更新/结算后调用）。"""
        if not hasattr(self, "lbl_rounds"):
            return  # 控件尚未构建完成时忽略
        lines = []
        if self.session and self.engine.is_active():
            for info in self.session.all_rounds():
                gid = str(info["group_id"])
                last = self._last_settle.get(gid)
                tail = f"｜ 上次结算 {last['time']} " if last else ""
                if last:
                    tail += ("成功" if last.get("ok") else "失败")
                lines.append(
                    f"群{gid}：局 {info['label']}（{info['round_id']}）｜ "
                    f"事件 {info['event_count']}/{HARD_LIMIT} ｜ 净变动 {info['total_delta']:+d}"
                    f"{tail if last else '｜ 未结算'}")
        elif self.session and self.session.pending_count():
            for info in self.session.all_rounds():
                lines.append(f"群{info['group_id']}：局 {info['label']} 未上报（事件保留，待「重试未上报」）")
        if lines:
            self.lbl_rounds.configure(text="\n".join(lines), text_color="#2ecc71")
        else:
            self.lbl_rounds.configure(text="", text_color="gray")

        # 每行勾选框旁的状态文字（局/事件/净积分）
        for g, lbl in getattr(self, "_group_labels", {}).items():
            info = None
            if self.session and self.engine.is_active():
                info = self.session.round_info(g)
            if self.engine.is_active() and g in self._active_groups:
                if info:
                    lbl.configure(text=f"局 {info['label']}｜事件 {info['event_count']}｜净积分 {info['total_delta']:+d}",
                                  text_color="#2ecc71")
                else:
                    lbl.configure(text="待开局", text_color="gray")
            elif self.engine.is_active():
                lbl.configure(text="未参与", text_color="gray")
            else:
                lbl.configure(text="", text_color="gray")

    def _apply_state(self) -> None:
        if self._server_error:
            self.lbl_state.configure(text=f"回调服务异常：{self._server_error}", text_color="red")
            return
        port = int(self.cfg.play_callback_port or 6101)
        if self._banned_reason:
            self.lbl_state.configure(
                text=f"玩法停摆（执行器被封禁/操作员停用）｜ {self._banned_reason}\n"
                     f"未上报对局已保留，待总后台解封后可「重试未上报」",
                text_color="red")
        elif self.engine.is_active():
            groups = ",".join(sorted(self._active_groups)) or str(self.cfg.play_group_id or "")
            self.lbl_state.configure(
                text=(f"运行中 ｜ 玩法 {self.engine.rule_summary()} ｜ 游戏群 {groups}"
                      f"（结算=「结算并上报」，幂等可重复）"),
                text_color="green")
        else:
            self.lbl_state.configure(text="未启用玩法（勾选游戏群后点「启用玩法」）", text_color="gray")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy

    def _log(self, text: str) -> None:
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", text + "\n")
            lines = int(self.txt_log.index("end-1c").split(".")[0])
            if lines > 500:
                self.txt_log.delete("1.0", f"{lines - 400}.0")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def shutdown(self) -> None:
        """关窗清理：停 worker + 回调服务（防残留 daemon 线程）。未上报局仍在内存，关窗即丢 ——
        建议关窗前「结算并上报」；被封禁期间的残留局请先解封再结算。"""
        self._stop.set()
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass
        for t in (self._worker, self._server_thread):
            if t and t.is_alive():
                t.join(timeout=2)
