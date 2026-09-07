"""第 8 个 Tab「游戏玩法」：复合玩法手动驱动操作台（唯一玩法，多群各开各局）。

能力（v3）：
- 启动后自动激活唯一玩法（复合玩法）：拉总后台 active 玩法列表 → 挑名字含「复合」的玩法
  （没有则取第一条）→ 下载到本地 plays/ 缓存 → 插件转发配置（有游戏群时）→ 本地引擎激活
- 多群各开各局：「当前群」切换器决定按钮与开奖表作用对象；开始本局 / 停止下注 / 结算 /
  作废本局 四个手动按钮驱动本局生命周期（各群独立，互不影响）
- 开奖表（右侧半宽可编辑）：双击「抢到金额」修改或补填 0.01~0.99（点数随之重算）；
  下注列不可改；结算成功后结果保留展示到下次开局
- 管理员发红包开奖：红包领取事件进玩法判定（首红包/领完即「可结算」），份数不足自动作废；
  「结算」= 玩法批量结算 → 上报总后台（round_id 幂等）→ 成功才 @全体 播报
- 玩法协议 v2：handle_message(group_id, qq, nickname, text) 可返回
  None / 回复文本 / (回复, delta) / {"reply":…, "delta":…}（str 单返回=旧协议 delta=0）
- 执行器被封禁（40310）/操作员停用（40311）→ 玩法停摆红字，待解封后重试上报
- 重启 GUI 自动恢复：重新下载玩法最新版 + 多群转发 + 自动激活

线程纪律：所有网络/引擎操作在临时 daemon 线程，UI 一律 after(0) 回主线程改控件
（与 backend_tab / main_window 同款模式）。
"""
from __future__ import annotations

import glob
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import ttk
from typing import Any, Callable

import customtkinter as ctk
import requests

from integration.backend_client import BackendError, ExecutorBanned, HbjfClient, OperatorDisabled
from integration.member_sync import group_create_time_str, run_member_sync
from integration.play_engine import FLUSH_LIMIT, HARD_LIMIT, RoundSession, RuleEngine
from integration.redpacket_game import RedPacketGame

import os as _os
import datetime as _dt
from pathlib import Path as _Path

_PLAY_LOG_PATH = _Path(_os.environ.get("APPDATA") or _Path.home()) / "QQHongbaoMonitor" / "logs" / "play.log"


def _flog(text: str) -> None:
    """玩法运行日志落盘（排查红包玩法链路用；线程安全追加，超 2MB 截断）。"""
    try:
        _PLAY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _PLAY_LOG_PATH.exists() and _PLAY_LOG_PATH.stat().st_size > 2 * 1024 * 1024:
            _PLAY_LOG_PATH.unlink()
        with _PLAY_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now().strftime('%m-%d %H:%M:%S')}] {text}\n")
    except Exception:
        pass

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
        self.session: RoundSession | None = None  # 复合玩法不建对局会话（保持 None，旧引用判空不触发）
        self.rp_game: RedPacketGame | None = None  # 红包计分玩法（玩法含 handle_redpacket 时启用）
        self._admin_cache: dict[str, tuple[float, set[str]]] = {}
        self._points_cache: dict[str, tuple[float, int]] = {}
        self._base_cooldown: dict[str, float] = {}  # 无效指令按群冷却
        self._query_cooldown: dict[str, float] = {}  # 查分按 群:qq 冷却
        self._active_groups: set[str] = set()     # 已启用转发的游戏群
        self._cur_group: str = ""                # 当前操作群
        self._table_rows: dict[str, list] = {}   # 群 -> 表格行缓存（结算后保留展示用）
        self._table_live: set[str] = set()       # 群 -> 有活动本局（live 数据源有效）
        self._msg_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._server_error = ""
        self._busy = False
        self._banned_reason: str = ""               # 非空=玩法已停摆（红字展示）
        self._last_settle: dict[str, dict] = {}     # gid -> {ok, error, time}
        self._payload_cache: dict[str, tuple[float, dict]] = {}  # 群成员 payload 30s 缓存

        self.on_approve = None  # MainWindow 挂接审批页 push
        self._build_ui()
        self._start_callback_server()
        # GUI 启动时自动激活唯一玩法（复合玩法）
        self.after(400, self._auto_restore)

    # ================= 界面构建 =================

    def _build_ui(self) -> None:
        # ---- 引擎状态 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=(2, 6))
        ctk.CTkLabel(card, text="复合玩法操作台（多群各开各局；按钮与开奖表只作用于「当前群」）",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 2))
        self.lbl_state = ctk.CTkLabel(card, text="初始化中…", text_color="orange", wraplength=760,
                                      justify="left", anchor="w")
        self.lbl_state.pack(fill="x", padx=10, pady=(2, 4))

        # ---- 费率 / 最小下注 / 游戏群 / 本局操作 ----
        card = ctk.CTkFrame(self)
        card.pack(fill="x", padx=4, pady=6)

        fee_row = ctk.CTkFrame(card, fg_color="transparent")
        fee_row.pack(fill="x", padx=10, pady=(8, 2))
        ctk.CTkLabel(fee_row, text="游戏费率(‰)", width=96, anchor="w").pack(side="left")
        self.entry_fee_rate = ctk.CTkEntry(fee_row, width=70)
        self.entry_fee_rate.insert(0, str(int(getattr(self.cfg, "game_fee_rate", 20) or 20)))
        self.entry_fee_rate.pack(side="left", padx=4)
        self.entry_fee_rate.bind("<Return>", lambda _e: self._save_fee_rate())
        ctk.CTkLabel(fee_row, text="（20=2%，红包玩法抽水比例；修改回车立即上报总后台）",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left", padx=6)
        ctk.CTkButton(fee_row, text="保存费率", width=90, command=self._save_fee_rate).pack(side="left", padx=4)

        bet_row = ctk.CTkFrame(card, fg_color="transparent")
        bet_row.pack(fill="x", padx=10, pady=(2, 2))
        ctk.CTkLabel(bet_row, text="最小下注(积分)", width=96, anchor="w").pack(side="left")
        self.entry_min_bet = ctk.CTkEntry(bet_row, width=70)
        self.entry_min_bet.insert(0, str(int(getattr(self.cfg, "play_min_bet", 10) or 10)))
        self.entry_min_bet.pack(side="left", padx=4)
        self.entry_min_bet.bind("<Return>", lambda _e: self._save_min_bet())
        ctk.CTkLabel(bet_row, text="（下注玩法：玩家直接发数字下注（如 500），低于此值或超过自己当前积分会被拒绝并提示）",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left", padx=6)
        ctk.CTkButton(bet_row, text="保存", width=70, command=self._save_min_bet).pack(side="left", padx=4)

        # ---- 游戏群勾选（多群，运行中可随时增删）----
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(row, text="游戏群（勾选参与玩法的群，可多选）", width=230, anchor="w").pack(side="left")
        ctk.CTkButton(row, text="刷新群列表", width=100, command=self._refresh_group_checkboxes).pack(side="left", padx=4)
        self.var_auto_members = ctk.BooleanVar(value=self.cfg.play_auto_register_members)
        ctk.CTkCheckBox(row, text="启用时自动注册群成员为会员", variable=self.var_auto_members,
                        command=self._save_auto_members).pack(side="left", padx=8)
        ctk.CTkLabel(row, text="（群号在「群管理」页维护，运行中勾选/取消即时生效）",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(side="left")

        self.frame_group_list = ctk.CTkScrollableFrame(card, height=120)
        self.frame_group_list.pack(fill="x", padx=10, pady=(4, 2))
        self._group_vars: dict[str, ctk.BooleanVar] = {}
        self._group_labels: dict[str, ctk.CTkLabel] = {}

        # ---- 按钮行：当前群切换器 + 本局操作 ----
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkLabel(row, text="当前群", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(0, 6))
        self.opt_group = ctk.CTkOptionMenu(row, values=["（无游戏群）"], width=160,
                                           command=self._on_group_picked)
        self.opt_group.pack(side="left", padx=(0, 12))
        ctk.CTkButton(row, text="开始本局", width=90, command=self._start_round_cur).pack(side="left", padx=4)
        self.btn_seal = ctk.CTkButton(row, text="停止下注", width=90, command=self._seal_round_cur)
        self.btn_seal.pack(side="left", padx=4)
        self.btn_settle = ctk.CTkButton(row, text="结算", width=80, command=self._settle_round_cur)
        self.btn_settle.pack(side="left", padx=4)
        self.btn_void = ctk.CTkButton(row, text="作废本局", width=90, command=self._void_round_cur)
        self.btn_void.pack(side="left", padx=4)
        ctk.CTkButton(row, text="更新玩法文件", width=110, command=self._reload_rule).pack(side="left", padx=4)
        ctk.CTkButton(row, text="重试未上报", width=110, command=self._retry_rp_pending_ui).pack(side="left", padx=4)

        # ---- 各群当前局状态 ----
        self.lbl_rounds = ctk.CTkLabel(self, text="", text_color="#2ecc71", anchor="w", justify="left",
                                       wraplength=900, font=ctk.CTkFont(size=12))
        self.lbl_rounds.pack(fill="x", padx=14, pady=(0, 4))

        # ---- 下半区：左日志 / 右开奖表格 ----
        lower = ctk.CTkFrame(self)
        lower.pack(fill="both", expand=True, padx=4, pady=6)
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)
        card_l = ctk.CTkFrame(lower)
        card_l.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        card_r = ctk.CTkFrame(lower)
        card_r.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        ctk.CTkLabel(card_l, text="玩法日志（发言 / 开奖 / 上报结果）",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self.txt_log = ctk.CTkTextbox(card_l, height=240, state="disabled",
                                      font=ctk.CTkFont(size=11))
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        top_r = ctk.CTkFrame(card_r, fg_color="transparent")
        top_r.pack(fill="x", padx=10, pady=(6, 2))
        ctk.CTkLabel(top_r, text="本局开奖表（双击「抢到金额」可修改；下注不可改）｜ 当前群：",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.lbl_table_group = ctk.CTkLabel(top_r, text="", text_color="#2ecc71",
                                            font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_table_group.pack(side="left", padx=4)
        table_frame = ctk.CTkFrame(card_r)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        cols = ("nick", "bet", "amount", "pts", "result")
        heads = ("昵称", "下注(积分)", "抢到金额(元)", "点数", "结算结果")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=8)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=70 if c != "nick" else 90, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_table_dclick)
        self.lbl_table_tip = ctk.CTkLabel(card_r, text="", text_color="gray", anchor="w",
                                          font=ctk.CTkFont(size=11), wraplength=480, justify="left")
        self.lbl_table_tip.pack(fill="x", padx=10, pady=(0, 4))

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
                    path = self.path.rstrip("/")
                    if path not in ("/play/msg", "/play/redpacket", "/play/approve"):
                        self.send_response(404)
                        self.end_headers()
                        return
                    length = int(self.headers.get("content-length") or 0)
                    raw = self.rfile.read(length) if length else b""
                    data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
                    if path == "/play/redpacket":
                        app._on_callback_redpacket(data)
                    elif path == "/play/approve":
                        app._on_callback_approve(data)
                    else:
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

    def _on_callback_approve(self, data: dict) -> None:
        """HTTP 线程：上/下积分申请 → 审批页（on_approve 由 MainWindow 挂接）。"""
        handler = getattr(self, "on_approve", None)
        if callable(handler):
            try:
                handler(data)
            except Exception:
                pass

    def _on_callback_redpacket(self, data: dict) -> None:
        """HTTP 线程：红包领取事件入队（红包计分玩法）。"""
        group_id = str(data.get("group_id") or "")
        bill_no = str(data.get("bill_no") or "")
        _flog(f"收到红包事件 group={group_id} bill={bill_no} sender={data.get('sender_uin')} "
              f"total={data.get('total_num')} recv={data.get('recv_num')} "
              f"claims={len(data.get('claims') or [])}")
        if not group_id or not bill_no:
            return
        self._msg_queue.put({"kind": "redpacket", "payload": data})

    def _worker_loop(self) -> None:
        """消息 worker：过玩法引擎 → 需要回复时发回群里（复合玩法积分只经红包结算产生）。"""
        while not self._stop.is_set():
            try:
                item = self._msg_queue.get(timeout=0.5)
            except queue.Empty:
                self._maybe_retry_rp_pending()
                continue
            try:
                if item.get("kind") == "redpacket":
                    self._handle_redpacket(item.get("payload") or {})
                else:
                    self._handle_one(item)
            except Exception as e:  # noqa: BLE001 — 引擎链路任何异常都不能停 worker
                self.after(0, self._log, f"✗ 消息处理异常: {e}")

    # ================= 红包计分玩法 =================

    def _maybe_retry_rp_pending(self) -> None:
        """每 30 秒：有未结算的红包局自动重试（token 修复/网络恢复后自动补结算）。"""
        game = self.rp_game
        if game is None:
            return
        try:
            game.poll_auto_end()
        except (ExecutorBanned, OperatorDisabled) as e:
            self._mark_banned(e)
            return
        except Exception:
            pass
        if not game.pending:
            return
        now = time.time()
        if now - getattr(self, "_last_rp_retry", 0.0) < 30:
            return
        self._last_rp_retry = now
        try:
            game.retry_pending()
        except (ExecutorBanned, OperatorDisabled) as e:
            self._mark_banned(e)
        except Exception as e:  # noqa: BLE001
            self.after(0, self._log, f"✗ 红包局重试异常: {e}")
        else:
            self.after(0, self._table_refresh)
            self.after(0, self._rounds_refresh)

    # ================= 操作台：当前群切换器 + 开奖表格 =================

    def _cur_groups(self) -> list[str]:
        """「当前群」可选值：已激活游戏群优先，其次配置的游戏群/监控群。"""
        groups: list[str] = []
        for g in list(self._active_groups) + self.cfg.play_group_list() \
                 + self.cfg.watch_group_list():
            if g not in groups:
                groups.append(g)
        return groups

    def _refresh_group_optmenu(self) -> None:
        groups = self._cur_groups()
        self.opt_group.configure(values=groups or ["（无游戏群）"])
        cur = self.cfg.play_group_current
        if not cur or cur not in groups:
            cur = groups[0] if groups else ""
        self._set_cur_group(cur)

    def _set_cur_group(self, gid: str) -> None:
        if not gid:
            self._cur_group = ""
            self.opt_group.set("（无游戏群）")
        else:
            self._cur_group = gid
            self.opt_group.set(gid)
            if self.cfg.play_group_current != gid:
                self.cfg.play_group_current = gid
                self.save_config(self.cfg)
        self.lbl_table_group.configure(text=self._cur_group or "（未选）")
        self._table_refresh()
        self._gate_buttons()

    def _on_group_picked(self, choice: str) -> None:
        if choice and not choice.startswith("（"):
            self._set_cur_group(choice)

    def _table_rows_for(self, gid: str) -> list[dict]:
        """当前群表格行（live 数据 = 玩法 bet_snapshot + rpg claims 金额）。"""
        if not (self.engine.is_active() and self.rp_game is not None):
            return []
        snap = self.engine.call_rule("bet_snapshot", gid) or []
        if not snap and gid not in self.rp_game.announced:
            return []  # 无局
        ann = self.rp_game.announced.get(gid)
        rows = []
        for r in snap:
            qq = str(r.get("qq") or "")
            c = (ann or {}).get("claims", {}).get(qq) or {}
            amount = c.get("amount")
            rows.append({"qq": qq, "nickname": r.get("nickname") or qq,
                         "bet": "坐庄" if r.get("boss") else str(r.get("amount")),
                         "amount": amount, "pts": self._pts_preview(amount),
                         "edited": bool(c.get("edited")), "boss": bool(r.get("boss")),
                         "result": ""})
        return rows

    @staticmethod
    def _pts_preview(amount) -> str:
        """抢到金额 -> 点数预览（与玩法一致：(a+b)%10；无效显 ?）。"""
        try:
            a = float(amount or 0)
        except (TypeError, ValueError):
            return "?"
        if not (0 < a <= 0.99):
            return "?"
        s = f"{a:.2f}"
        return str((int(s[2]) + int(s[3])) % 10)

    def _render_table(self, rows: list[dict]) -> None:
        """把行渲染进 tree（覆盖旧行）；amount 为空显示「未抢」，结果列原样。"""
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tree.tag_configure("warn", foreground="#c0392b")
        for r in rows:
            amt = r.get("amount")
            tags = []
            if not isinstance(amt, (int, float)) or not (0 < float(amt) <= 0.99):
                tags.append("warn")
            self.tree.insert("", "end", iid=r["qq"], values=(
                r["nickname"], r["bet"],
                "未抢" if not isinstance(amt, (int, float)) else f"{amt:.2f}",
                r["pts"], r["result"]), tags=tags)

    def _table_refresh(self) -> None:
        """刷新当前群表格与状态提示行。

        有活动本局 → 取 live 行并更新缓存；无活动本局 → 渲染上次缓存（结算后保留展示，
        spec §2.1），直到「开始本局」清空。"""
        gid = self._cur_group
        if not gid or not hasattr(self, "tree"):
            return
        rows = self._table_rows_for(gid) if gid else []
        if rows:
            self._table_rows[gid] = rows
            self._table_live.add(gid)
        elif gid in self._table_rows and self._table_rows[gid]:
            rows = self._table_rows[gid]
            self._table_live.discard(gid)
        else:
            rows = []
        self._render_table(rows)
        self.lbl_table_tip.configure(text=self._cur_round_state_txt(gid))

    def _cur_round_state_txt(self, gid: str) -> str:
        """当前群局状态提示行（供表格下说明条；无局/无玩法给引导文案）。"""
        if not (self.engine.is_active() and self.rp_game is not None):
            return "玩法未激活"
        if gid not in self.rp_game.announced:
            if gid in self._table_rows and self._table_rows[gid]:
                return "上一局已结束（结算结果如上）；点「开始本局」开新局"
            return "无进行中本局：点「开始本局」开局（将 @全体 播报开始消息）"
        info = self.engine.call_rule("seal_info", gid) or {}
        phase = info.get("phase") or "idle"
        rid = info.get("round_id") or ""
        ann = self.rp_game.announced[gid]
        fb = ann.get("first_bill")
        need = int(info.get("claim_need") or 0)
        n_bets = int(info.get("n_bets") or 0)
        if phase == "betting":
            fb_txt = f"｜首红包 {fb['total_num']}/{need} 份" if fb else ""
            return f"下注期 ｜ 局 {rid} ｜ 已下注 {n_bets} 人，需开奖 {need} 人{fb_txt}"
        if ann.get("ready_to_settle"):
            return f"已封盘 ｜ 红包已领完 → 可点「结算」（也可先双击表格改/补开奖金额）"
        if fb:
            return (f"已封盘 ｜ 红包 {fb.get('total_num')} 份（需 {need}）"
                    f"{fb.get('recv_num')} 人已领，领完即可结算；份数不足将自动作废")
        return f"已封盘 ｜ 局 {rid}：等管理员发红包开奖（份数≥{need}，每份 0.01~0.99 元）"

    def _on_table_dclick(self, event=None) -> None:
        """双击表格：仅「抢到金额」列（#3）弹窗改值；未抢者=补值（视同抢到）。"""
        gid = self._cur_group
        if not gid or not (self.rp_game and gid in self.rp_game.announced):
            self._log("✗ 当前群没有可编辑的本局（先「开始本局」）")
            return
        if not event or self.tree.identify_column(event.x) != "#3":
            return
        sel = self.tree.selection()
        if not sel:
            return
        info = self.engine.call_rule("seal_info", gid) or {}
        if info.get("phase") not in ("betting", "sealed"):
            self._log("✗ 当前局不在下注/封盘期，不能改开奖")
            return
        qq = sel[0]
        win = ctk.CTkToplevel(self)
        win.title(f"改开奖金额 - {gid}")
        win.geometry("340x150")
        ctk.CTkLabel(win, text="新抢到金额（元，0.01~0.99，两位小数）：").pack(padx=12, pady=(12, 2))
        entry = ctk.CTkEntry(win)
        entry.pack(padx=12, fill="x")
        ctk.CTkLabel(win, text="只改开奖金额（点数随之变化）；下注不可改；未抢者可在此补值",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(padx=12, pady=(2, 6))

        def ok() -> None:
            v = entry.get().strip()
            try:
                fv = round(float(v), 2)
            except ValueError:
                self._log("✗ 金额需为数字")
                return
            nick = next((r["nickname"] for r in self._table_rows.get(gid, [])
                         if r["qq"] == qq), qq)
            err = self.rp_game.set_claim_amount(gid, qq, fv, nick)
            if err:
                self._log(f"✗ 改开奖失败：{err}")
                return
            self._log(f"管理员改开奖：群{gid} {nick}({qq}) → {fv:.2f}（点 {self._pts_preview(fv)}）")
            win.destroy()
            self._table_refresh()

        ctk.CTkButton(win, text="确定", width=120, command=ok).pack(pady=6)
        entry.focus_set()

    # ================= 本局操作按钮（当前群） =================

    def _gate_buttons(self) -> None:
        """按当前群局状态开关 停止下注/结算/作废。开始本局按钮随 rp 有无 active 局切换。"""
        if not hasattr(self, "btn_seal"):
            return
        gid = self._cur_group
        active = bool(gid and self.engine.is_active() and self.rp_game is not None)
        info = self.engine.call_rule("seal_info", gid) if active else {}
        phase = (info or {}).get("phase") or "idle"
        has_ann = bool(active and self.rp_game and gid in self.rp_game.announced)
        self.btn_seal.configure(state="normal" if (active and has_ann and phase == "betting") else "disabled")
        self.btn_settle.configure(state="normal" if (active and has_ann and phase == "sealed") else "disabled")
        self.btn_void.configure(state="normal" if (active and has_ann) else "disabled")

    def _clear_round_display(self, gid: str) -> None:
        """开局前清空上一局表格展示缓存。"""
        self._table_rows.pop(gid, None)
        self._table_live.discard(gid)

    def _start_round_cur(self) -> None:
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            self._log("✗ 玩法未激活或未选游戏群（先勾选群并等自动激活）")
            return
        if self.rp_game.has_active_round(gid):
            self._log(f"✗ 群{gid} 已有进行中的本局，先结算或作废")
            return
        self._clear_round_display(gid)
        name = self.cfg.play_rule_name or "复合玩法"
        desc = "大吃小×撑庄：发数字下注；发「撑」即可撑庄；封盘后管理员发红包定大小"
        seq = int(getattr(self.cfg, "game_round_seq", 0) or 0) + 1
        # 局号纯数字递增（0000001…），不带玩法名前缀；后端 round_id 只是 ≤96 幂等键
        round_id = f"{seq:07d}"
        self.cfg.game_round_seq = seq
        self.save_config(self.cfg)
        self.rp_game.start_round(gid, round_id, name)
        text = self.engine.call_rule("handle_round_start", gid, round_id) or (
            f"游戏开始，游戏名称为{name}，游戏介绍为{desc}，游戏对局id为：{round_id}")
        self._rp_send_announce(gid, text)
        self._log(f"群{gid} 开始本局 {round_id}")
        self.after(0, self._table_refresh)
        self.after(0, self._rounds_refresh)

    def _seal_round_cur(self) -> None:
        """停止下注：玩法 handle_seal 汇总 → rp.seal_round（A3 判定）→ 未作废则 @全体 汇总。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        rate = int(getattr(self.cfg, "game_fee_rate", 20) or 20)
        ann = self.rp_game.announced.get(gid)
        if ann is None:
            self._log(f"✗ 群{gid} 没有进行中的本局（先「开始本局」）")
            return
        res = self.engine.call_rule("handle_seal", gid, rate)
        if not isinstance(res, dict):
            self._log(f"✗ 群{gid} 停止下注失败：玩法无响应")
            return
        ann["rate_permille"] = rate
        if not res.get("ok"):
            # handle_seal 的 ok=False 有三义（rule_fuhe.handle_seal 文案为准）：
            #  1) 含「无人下注」——玩法已复位（本局已作废）：播报作废并关局；
            #  2) 含「没有进行中的本局」——玩法无局（热重载脱钩等）：只本地日志并关局清表，
            #     不发群（这是 agent 侧状态问题，群成员无需看到「无法停止下注」噪音，
            #     真实原因在玩法日志可见）；
            #  3) 含「已封盘」（「本局已封盘或不在下注期」）——状态保留（本局已封盘）：
            #     只日志提示并刷新，不再 void/清表（双击「停止下注」/按钮重试期间再点
            #     不会把刚封盘的局作废并播误导文案，幂等友好）。
            text = str(res.get("text") or "本局已作废")
            if "无人下注" in text:
                self._rp_send_announce(gid, text)
                self.rp_game.void_round(gid, None)
                self._clear_round_display(gid)
                self.after(0, self._table_refresh)
                self.after(0, self._rounds_refresh)
            elif "没有进行中的本局" in text:
                self._log(f"群{gid} {text}（本地关局清表，不发群）")
                self.rp_game.void_round(gid, None)
                self._clear_round_display(gid)
                self.after(0, self._table_refresh)
                self.after(0, self._rounds_refresh)
            else:
                self._log(f"群{gid} {text}")
                self._table_refresh()
                self._rounds_refresh()
            return
        self.rp_game.seal_round(gid)   # A3：封盘时首红包已知不足 → 内部直接作废播报
        if gid not in self.rp_game.announced:  # 已被 seal_round 作废
            self._clear_round_display(gid)
            self.after(0, self._table_refresh)
            self.after(0, self._rounds_refresh)
            return
        bc = res.get("banker_check")
        if bc:
            qq = str(bc.get("qq") or "")
            need = int(bc.get("need") or 0)
            balance = self._query_points(qq)  # 30s 缓存查询（spec 开放项默认接受）
            if balance < need:
                # 播报庄家余额不足详情（spec §2.2 模板）；handle_void 照调只用于复位玩法状态
                # （该分支下规则状态必存在，handle_void 恒返回通用作废文案，不取用）
                self.engine.call_rule("handle_void", gid)
                self._rp_send_announce(gid, f"庄家 {bc.get('nickname') or qq} 余额不足"
                                        f"（需 {need}，实际 {balance}），本局作废（积分未扣）")
                self.rp_game.void_round(gid, None)
                self._clear_round_display(gid)
                self._log(f"群{gid} 庄家余额 {balance} < 需 {need}，本局作废")
                self.after(0, self._table_refresh)
                self.after(0, self._rounds_refresh)
                return
        self._rp_send_announce(gid, str(res.get("text") or ""), res.get("img"))
        self._log(f"群{gid} 已停止下注（封盘），等管理员发红包开奖")
        self.after(0, self._table_refresh)
        self.after(0, self._rounds_refresh)

    def _settle_round_cur(self) -> None:
        """结算（当前群）：未领完需二次确认（强结 = 未领者按 0 输光，spec D3）。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        info = self.engine.call_rule("seal_info", gid) or {}
        if info.get("phase") != "sealed":
            self._log(f"✗ 群{gid} 未封盘，不能结算（先「停止下注」）")
            return
        ann = self.rp_game.announced.get(gid)
        if ann is None:
            self._log(f"✗ 群{gid} 没有本局会话")
            return
        need = int(info.get("claim_need") or 0)
        fb = ann.get("first_bill")
        if not ann.get("ready_to_settle"):
            if not fb:
                # 尚未见到红包事件：手动补值已齐（=需开奖人数）→ 与「未领完」同款二次确认后
                # 放行结算；否则只引导「作废/等红包」，不再承诺补值可结算（除非补满）。
                # 已确认（_force_ok）且补满 → 落空穿过 fb 逻辑，直接放行到下方结算。
                if len(ann.get("claims") or {}) >= need:
                    if not ann.get("_force_ok"):  # 第一次点：警告 + 放行标志
                        ann["_force_ok"] = True
                        self._log(f"⚠ 群{gid} 尚未见到红包事件（手动补值 {len(ann['claims'])}/{need} 已齐），"
                                  f"未补者将按 0 结算（输光）；确认则再点一次「结算」")
                        return
                else:
                    self._log(f"⚠ 群{gid} 尚未见到红包事件，不能结算：可先「作废本局」或等待红包到达"
                              f"（或双击表格把开奖金额补满 {need} 人后再结算）")
                    return
            else:
                fb_n = int(fb.get("total_num") or 0)
                if fb_n < need:
                    self._log(f"✗ 群{gid} 红包 {fb_n} 份 < 需开奖 {need} 人，本局应作废（不能结算）")
                    return
                if not ann.get("_force_ok"):  # 第一次点：警告 + 放行标志
                    ann["_force_ok"] = True
                    self._log(f"⚠ 群{gid} 红包未领完（{fb.get('recv_num')}/{fb_n}），"
                              f"未领者将按 0 结算（输光）；确认则再点一次「结算」")
                    return
        self._log_sep()
        self._table_refresh()   # 结算前定格当前开奖表（供结算成功后展示）
        self._set_busy(True)

        def run() -> None:
            try:
                r = self.rp_game.settle_round_now(gid)
            except (ExecutorBanned, OperatorDisabled) as e:
                self.after(0, self._mark_banned, e)
                return
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            if r.get("ok"):
                self.after(0, self._after_settle_ok, gid, r.get("results") or [])
            else:
                self.after(0, self._log,
                           f"✗ 群{gid} 结算失败：{r.get('error')}（可「重试未上报」）")
                self.after(0, self._rounds_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-settle-cur", daemon=True).start()

    def _after_settle_ok(self, gid: str, results: list[dict]) -> None:
        """结算成功：把 delta 填进缓存行（结果列），表格保留展示到下次开局（spec §2.1）。"""
        dmap = {r["qq"]: r for r in results}
        rows = []
        for r in self._table_rows.get(gid, []):
            dr = dmap.get(r["qq"])
            r = dict(r)
            r["result"] = "" if dr is None else (f"{dr['delta']:+d}" if dr["delta"] else "平")
            rows.append(r)
        self._table_rows[gid] = rows
        self._log(f"✓ 群{gid} 本局结算成功并已播报，结果保留在开奖表（下次开局清空）")
        self._table_refresh()
        self._rounds_refresh()

    def _void_round_cur(self) -> None:
        """作废本局（当前群）：玩法 handle_void 文案 → @全体 播报 → 关局，不上报。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        if not self.rp_game.has_active_round(gid):
            self._log("当前群没有进行中的本局")
            return
        text = self.engine.call_rule("handle_void", gid) or f"本局 {gid} 已作废（积分未扣）"
        self._rp_send_announce(gid, text)
        self.rp_game.void_round(gid, None)
        self._clear_round_display(gid)
        self._log(f"群{gid} 本局已作废（不上报）")
        self.after(0, self._table_refresh)
        self.after(0, self._rounds_refresh)

    # ================= 基础玩法（监控群常驻） =================

    def _base_approve_confirm(self, text: str) -> str:
        """上/下申请确认语（局内玩法未处理时的兜底；玩家发「上1000/下100」，「上分/下分」旧词兼容）。"""
        m = re.match(r"^(上|下)(?:分)?\s*(\d+)$", (text or "").strip())
        if m:
            return f"{m.group(1)}{m.group(2)}申请已提交，等待管理员审批"
        return ""

    def _query_points_reply(self, gid: str, qq: str, text: str) -> str:
        """内置查分：用户发「查 / 查分 / 查积分」→ 回复其当前积分。

        规则层之上的常驻能力：任何群状态（玩法启停/开局与否）都先于玩法处理，
        不进玩法、不入对局。每人 3s 冷却防刷屏；总后台未配置时给可操作提示。"""
        if (text or "").strip() not in ("查", "查分", "查积分"):
            return ""
        key = f"{gid}:{qq}"
        now = time.time()
        if now - self._query_cooldown.get(key, 0.0) < 3:
            return ""
        self._query_cooldown[key] = now
        if not (self.cfg.backend_base and self.cfg.backend_api_key):
            return "无法查询积分：总后台连接未配置"
        return f"当前积分：{self._query_points(qq)}"

    def _base_play_reply(self, gid: str, text: str) -> str:
        """局外基础玩法：上/下申请确认、下注提示、无效指令（冷却防刷屏）。"""
        t = (text or "").strip()
        m = re.match(r"^(上|下)(?:分)?\s*(\d+)$", t)
        if m:
            return f"{m.group(1)}{m.group(2)}申请已提交，等待管理员审批"
        if re.match(r"^(压|下注|投注|押)\s*\d+", t):
            return "不在游戏局内，无法下注（请等管理员在玩法面板点「开始本局」）"
        now = time.time()
        if now - self._base_cooldown.get(gid, 0.0) >= 10:
            self._base_cooldown[gid] = now
            return "无效指令（可发「上100/下100」「查分」查看积分，开局后直接发数字参与游戏（如 500））"
        return ""

    def _send_base_reply(self, gid: str, qq: str, text: str) -> None:
        """基础玩法回复（监控群放行，无需玩法开关）。"""
        try:
            r = requests.post(self.cfg.plugin_api("play/reply"),
                              json={"group_id": gid, "at_qq": qq, "text": text}, timeout=5)
            if not r.ok:
                self.after(0, self._log, f"✗ 基础玩法回复失败 HTTP {r.status_code}")
        except requests.RequestException as e:
            self.after(0, self._log, f"✗ 基础玩法回复失败: {e}")

    def _handle_redpacket(self, payload: dict) -> None:
        """红包领取事件 → 红包计分玩法（worker 线程）。"""
        game = self.rp_game
        if game is None or not self.engine.is_active():
            _flog(f"忽略红包事件（玩法未启用或无红包协议）group={payload.get('group_id')}")
            return
        # 把当前游戏费率带给玩法（批量结算抽水用）
        payload["rate_permille"] = int(getattr(self.cfg, "game_fee_rate", 20) or 20)
        gid = str(payload.get("group_id") or "")
        if gid not in self._active_groups:
            _flog(f"忽略红包事件（群不在玩法名单）group={gid} active={sorted(self._active_groups)}")
            return
        try:
            data = self._fetch_payload(gid) or {}
            payload["member_count"] = int(data.get("member_count") or 0)
        except Exception:
            payload["member_count"] = 0
        had = gid in game.announced
        try:
            game.retry_pending()
            game.handle_claim(payload)
            if had and gid not in game.announced:
                # 局中作废（A3 红包份数不足 / retry 补结算成功清局）：清掉本局表格展示缓存
                self._clear_round_display(gid)
            self.after(0, self._table_refresh)
            self.after(0, self._rounds_refresh)
        except (ExecutorBanned, OperatorDisabled) as e:
            self._mark_banned(e)
        except Exception as e:  # noqa: BLE001 — 单包事件异常不影响后续
            self.after(0, self._log, f"✗ 红包计分处理异常: {e}")

    def _rp_rule_handler_batch(self, gid: str, claims: list[dict], rate: int) -> dict | None:
        """红包领完时的批量结算（玩法 settle_redpacket 协议，带费率）。"""
        return self.engine.handle_redpacket_batch(gid, claims, rate)

    def _rp_send_reply(self, gid: str, qq: str, text: str) -> None:
        """@领取人 回复（红包计分即时反馈）。"""
        try:
            requests.post(self.cfg.plugin_api("play/reply"),
                          json={"group_id": gid, "at_qq": qq, "text": text}, timeout=5)
        except requests.RequestException as e:
            self.after(0, self._log, f"✗ 红包回复发送失败: {e}")

    def _rp_send_announce(self, gid: str, text: str, img: dict | None = None) -> None:
        """@全体 播报红包结算结果。

        img（玩法规则返回的 img 键）可用时：文字段只发 caption 短句 + 表格图片；
        渲染失败/行数超限/无字体 → 降级整段纯文本表格；图片发送被拒 → 自动改发纯文本。"""
        payload = {"group_id": gid, "text": str(text or "")}
        if isinstance(img, dict) and img.get("rows"):
            try:
                from .table_image import render_table_png
                png = render_table_png(img)
                if png:
                    path = self._announce_img_file(gid)
                    with open(path, "wb") as f:
                        f.write(png)
                    payload["text"] = str(img.get("caption") or text or "")
                    payload["image_file"] = str(path)
            except Exception as e:  # noqa: BLE001 — 图片链路任何异常都降级纯文本
                payload.pop("image_file", None)
                payload["text"] = str(text or "")
                self.after(0, self._log, f"⚠ 表格图片渲染失败，降级纯文本: {e}")
        try:
            r = requests.post(self.cfg.plugin_api("play/announce"),
                              json=payload, timeout=10)
            if r.ok:
                tag = "（带表格图片）" if payload.get("image_file") else ""
                self.after(0, self._log, f"✓ 已@全体播报红包结算（群 {gid}）{tag}")
                return
            self.after(0, self._log,
                       f"✗ @全体播报失败 HTTP {r.status_code}: {r.text[:120]}")
            # 图片发送被插件拒绝（路径不可读等）→ 改发纯文本兜底，公告不丢
            if payload.get("image_file"):
                r2 = requests.post(self.cfg.plugin_api("play/announce"),
                                   json={"group_id": gid, "text": str(text or "")},
                                   timeout=5)
                if r2.ok:
                    self.after(0, self._log, "✓ 图片被拒，已改发纯文本公告")
                else:
                    self.after(0, self._log,
                               f"✗ 纯文本公告也失败 HTTP {r2.status_code}")
        except requests.RequestException as e:
            self.after(0, self._log, f"✗ @全体播报失败: {e}")

    def _announce_img_file(self, gid: str) -> str:
        """表格图片临时 PNG（唯一文件名；顺手清掉 10 分钟前的旧公告图防堆积）。

        agent 与 NapCat 同机同用户运行，插件直接读本地绝对路径发图。"""
        import tempfile
        d = tempfile.gettempdir()
        try:
            now = time.time()
            for old in glob.glob(os.path.join(d, "hbjf_ann_*.png")):
                try:
                    if now - os.path.getmtime(old) > 600:
                        os.remove(old)
                except OSError:
                    pass
        except Exception:  # noqa: BLE001 — 清理失败不影响发送
            pass
        return os.path.join(d, f"hbjf_ann_{gid}_{int(time.time() * 1000)}.png")

    def _rp_settle(self, round_id: str, gid: str, events: list[dict]) -> None:
        """红包局上报总后台（round_id=红包单号，幂等）。"""
        res = self._client().report_game_round(
            round_id, self.cfg.play_rule_name or "红包玩法", gid, events,
            play_id=self.cfg.play_rule_id or 0)
        warn = res.get("warning") or ""
        self.after(0, self._log,
                   f"✓ 红包局 {str(round_id)[:16]} 已上报：事件 {res.get('event_count')} ｜ "
                   f"净积分 {res.get('total_delta')}"
                   + (f" ｜ 注意: {warn}" if warn else "")
                   + ("（重复上报，未重复入账）" if res.get("duplicate") else ""))

    def _query_points(self, qq: str) -> int:
        """查某成员当前积分（30s 缓存；查不到返回 0）。"""
        now = time.time()
        hit = self._points_cache.get(str(qq))
        if hit and now - hit[0] < 30:
            return hit[1]
        pts = 0
        client = self._backend_client_safe()
        if client:
            try:
                rows = client.query_points_batch([str(qq)])
                if rows and rows[0].get("exists"):
                    pts = int(rows[0].get("points") or 0)
            except Exception:
                pts = 0
        self._points_cache[str(qq)] = (now, pts)
        return pts

    def _backend_client_safe(self):
        """总后台客户端（未配置返回 None）。"""
        try:
            if not (self.cfg.backend_base and self.cfg.backend_api_key):
                return None
            return HbjfClient(self.cfg.backend_base, self.cfg.backend_api_key,
                              token=self.cfg.executor_token)
        except Exception:
            return None

    def _rp_admin_qqs(self, gid: str) -> set[str]:
        """群主+管理员 QQ 集合（600s 缓存；随 _fetch_payload 30s 缓存兜底）。"""
        now = time.time()
        hit = self._admin_cache.get(gid)
        if hit and now - hit[0] < 600:
            return hit[1]
        try:
            data = self._fetch_payload(gid) or {}
        except Exception:
            return set()
        admins = {str(m.get("uin") or "") for m in (data.get("members") or [])
                  if str(m.get("role") or "") in ("owner", "admin")}
        self._admin_cache[gid] = (now, admins)
        return admins

    def _rp_build(self, rule_id: int, rule_name: str) -> None:
        """玩法文件定义了 handle_redpacket 时自动启用红包玩法会话。"""
        self.rp_game = None
        if self.engine.has_redpacket_handler():
            self.rp_game = RedPacketGame(
                play_name=rule_name, play_id=rule_id,
                rule_handler=lambda g, q, n, a: self.engine.handle_redpacket(g, q, n, a),
                rule_handler_batch=self._rp_rule_handler_batch,
                send_reply=self._rp_send_reply,
                send_announce=self._rp_send_announce,
                settle=self._rp_settle,
                get_admin_qqs=self._rp_admin_qqs,
                get_bettors=lambda gid: self.engine.rule_bettors(gid),
                query_abort=lambda gid: self.engine.query_round_abort(gid),
                claim_need=lambda gid: int(self.engine.call_rule("claim_need", gid) or 0),
                on_log=lambda msg: self.after(0, self._log, msg),
                on_round_end=lambda gid: self.engine.notify_round_end(gid),
            )
            self._log("玩法含 handle_redpacket：红包计分玩法已启用")

    def _handle_one(self, item: dict) -> None:
        group_id = item["group_id"]
        qq = item["qq"]
        nickname = item.get("nickname") or ""
        text = item["text"]
        who = f"{nickname}({qq})" if nickname else f"QQ{qq}"

        # 内置查分：查 / 查分 / 查积分 → 回复当前积分（先于玩法与局状态，不入对局）
        query_reply = self._query_points_reply(str(group_id), str(qq), text)
        if query_reply:
            self._send_base_reply(str(group_id), str(qq), query_reply)
            self.after(0, self._log, f"基础玩法 群{group_id} {who}: 查分")
            return

        # 不在游戏局内 → 基础玩法兜底（监控群常驻）：
        # 上/下申请确认、局外下注提示、无法识别回复「无效指令」
        if not (self.engine.is_active() and group_id in self._active_groups):
            base_reply = self._base_play_reply(group_id, text)
            if base_reply:
                self._send_base_reply(group_id, qq, base_reply)
                self.after(0, self._log, f"基础玩法 群{group_id} {who}: {text[:40]}")
            return

        # 「开始游戏」只认群主/管理员（普通成员发的不进玩法）
        t_strip = (text or "").strip()
        if t_strip in ("开始游戏", "开局"):
            admins = self._rp_admin_qqs(group_id)
            if admins and qq not in admins:
                self.after(0, self._log, f"⚠ 群{group_id} {nickname or qq} 发「开始游戏」被忽略（非管理员）")
                return
        # 下注预检（进玩法前）：低于最小下注 / 超过当前积分 → 直接提示（不入玩法、不入对局）。
        # 显式「下注N」任何时候都拦；纯数字只在玩法明确处于下注期（betting_open=True）才拦，
        # 避免把群聊里的普通数字当下注误校验。
        bet_open = self.engine.betting_open(group_id) if self.engine.is_active() else None
        m_bet = re.match(r"^下注\s*(\d+)$", t_strip)
        if not m_bet and bet_open:
            m_bet = re.match(r"^(\d+)$", t_strip)
        if m_bet:
            amount = int(m_bet.group(1))
            min_bet = int(getattr(self.cfg, "play_min_bet", 10) or 10)
            if amount < min_bet:
                self._send_reply(group_id, qq, nickname, who,
                                 f"下注失败：金额不能小于最小下注 {min_bet} 积分", 0)
                return
            pts = self._query_points(qq)
            if amount > pts:
                self._send_reply(group_id, qq, nickname, who,
                                 f"下注失败：积分不足（当前 {pts} 积分，本次下注 {amount}）", 0)
                return
        reply = self.engine.handle_message(group_id, qq, nickname, text)
        delta = self.engine.last_delta if hasattr(self.engine, "last_delta") else 0
        # 局内玩法未处理的上/下 → 基础玩法确认回复（申请照常进审批页）
        if not reply:
            confirm = self._base_approve_confirm(text)
            if confirm:
                self._send_base_reply(group_id, qq, confirm)
                self.after(0, self._log, f"基础玩法 群{group_id} {who}: 申请确认")
                return
        # 余额占位符替换：玩法返回「剩余积分{balance}」时查总后台积分填充
        if reply and "{balance}" in reply:
            reply = reply.replace("{balance}", str(self._query_points(qq)))
        # 本局模式：把群内聊天过程记入回放（不计分）
        if self.rp_game is not None:
            self.rp_game.record_chat(group_id, qq, nickname, text)
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

    # ================= 配置 / 群管理 / 激活 =================

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
        """勾选/取消（运行中即时生效：加群或退群）。"""
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
            self._active_groups.discard(gid)
            self._push_play_groups()
            self._log(f"已退出游戏群 {gid}")
        # 勾选/取消即时落盘（重启 _auto_restore 按 play_group_list 恢复转发名单）
        self.cfg.play_group_id = ",".join(sorted(self._active_groups))
        self.save_config(self.cfg)
        self._apply_state()
        self._rounds_refresh()
        self._refresh_group_optmenu()

    def _save_fee_rate(self) -> None:
        """保存游戏费率（千分比）并立即上报总后台执行器。"""
        try:
            v = int(self.entry_fee_rate.get().strip())
            if not 0 <= v <= 1000:
                raise ValueError
        except ValueError:
            self._log("✗ 费率需为 0~1000 的整数（千分比，20=2%）")
            return
        self.cfg.game_fee_rate = v
        self.save_config(self.cfg)
        self._log(f"游戏费率已保存：{v / 10}%")

        def work():
            try:
                r = self._client().heartbeat(game_fee_rate=v)
                self.after(0, self._log,
                           f"✓ 游戏费率 {v / 10}% 已上报总后台（执行器 {r.get('name')}）")
            except BackendError as e:
                self.after(0, self._log, f"✗ 费率上报失败: {e}")
            except Exception as e:  # noqa: BLE001 — 未配置总后台等
                self.after(0, self._log, f"✗ 费率上报失败: {e}")

        threading.Thread(target=work, daemon=True).start()

    def _save_min_bet(self) -> None:
        """保存最小下注金额（积分；持久化本机配置，下注预检即时生效）。"""
        try:
            v = int(self.entry_min_bet.get().strip())
            if v < 1:
                raise ValueError
        except ValueError:
            self._log("✗ 最小下注需为正整数（积分）")
            return
        self.cfg.play_min_bet = v
        self.save_config(self.cfg)
        self._log(f"最小下注已保存：{v} 积分")

    def _save_auto_members(self) -> None:
        """自动注册群成员开关：勾选即时持久化（下次激活玩法时生效）。"""
        self.cfg.play_auto_register_members = bool(self.var_auto_members.get())
        self.save_config(self.cfg)

    def _backend_banned_groups(self, client: HbjfClient) -> set[str]:
        """总后台里 status=banned 的群（封禁群停玩：不允许再启用该群玩法）。"""
        try:
            rows = client.list_groups()
        except BackendError:
            return set()  # 拉不到群列表不阻塞（首次对接/无群库时允许）
        return {str(g.get("group_id")) for g in rows if str(g.get("status")) == "banned"}

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

    def _retry_rp_pending_ui(self) -> None:
        """「重试未上报」：重试红包玩法结算失败事件（网络/封禁恢复后补结算）。"""
        game = self.rp_game
        if game is None or not self.engine.is_active():
            self._log("✗ 玩法未激活（无红包玩法会话）")
            return
        self._set_busy(True)

        def run() -> None:
            try:
                game.retry_pending()
            except (ExecutorBanned, OperatorDisabled) as e:
                self.after(0, self._mark_banned, e)
                self.after(0, self._set_busy, False)
                return
            except Exception as e:  # noqa: BLE001
                self.after(0, self._log, f"✗ 重试未上报失败: {e}")
            self.after(0, self._rounds_refresh)
            self.after(0, self._table_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-retry", daemon=True).start()

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
            self.rp_game = None
            self._cur_group = ""
            self._table_rows.clear()
            self._table_live.clear()
            if hasattr(self, "tree"):
                self._render_table([])
            self._banned_reason = str(reason)
            self._active_groups = set()
            self._log(f"✗ 玩法停摆：{reason}。未上报对局已保留在本地，"
                      f"待总后台解封/停用解除后「重试未上报」或重新启用玩法")
            self._apply_state()
            self._gate_buttons()

        try:
            self.after(0, on_ui)
        except Exception:
            pass  # 关窗竞态时忽略

    def _auto_restore(self) -> None:
        """启动后自动激活唯一玩法（复合玩法）：拉总后台 active 玩法列表 → 挑名字含「复合」的
        玩法（没有则取第一条）→ 下载到 plays/rule_{id}.py → 插件转发配置（若有游戏群）→ 激活。
        其余行为（封禁预检/插件推送/成员同步/UI 刷新）与原 _auto_restore 逐行保持一致。"""
        # 与复选框默认态对齐（_refresh_group_checkboxes：玩法群为空时默认勾选监控群）
        groups = self.cfg.play_group_list() or self.cfg.watch_group_list()
        self._log("自动激活唯一玩法（复合玩法）…")

        def run() -> None:
            client = self._client()
            banned = self._backend_banned_groups(client)
            if banned & set(groups):
                self.after(0, self._log,
                           f"✗ 自动激活取消：群 {','.join(sorted(banned & set(groups)))} 已被总后台封禁")
                self.after(0, self._apply_state)
                return
            rule_id = int(getattr(self.cfg, "play_rule_id", 0) or 0)
            rule_name = str(self.cfg.play_rule_name or "")
            rows: list[dict] = []
            try:
                rows = client.list_rules() or []
            except BackendError as e:
                self.after(0, self._log, f"⚠ 拉取玩法列表失败: {e}（按上次配置激活）")
            if rows:
                for r in rows:
                    if "复合" in str(r.get("name") or ""):
                        rule_id, rule_name = int(r["id"]), str(r.get("name") or f"玩法#{r['id']}")
                        break
                if not rule_id:
                    r = rows[0]
                    rule_id, rule_name = int(r["id"]), str(r.get("name") or f"玩法#{r['id']}")
            if not rule_id:
                self.after(0, self._log, "✗ 无可用玩法：请先在总后台「玩法管理」上传/启用复合玩法")
                self.after(0, self._apply_state)
                return
            dest = self.cfg.plays_dir() / f"rule_{rule_id}.py"
            if rows:
                try:
                    client.download_rule(rule_id, str(dest))
                except BackendError as e:
                    self.after(0, self._log, f"✗ 下载玩法失败: {e}")
                    self.after(0, self._apply_state)
                    return
            if not dest.is_file():
                self.after(0, self._log, f"✗ 玩法文件缺失（本地缓存也没有）: #{rule_id}")
                self.after(0, self._apply_state)
                return
            if groups:
                callback = f"http://127.0.0.1:{int(self.cfg.play_callback_port or 6101)}/play/msg"
                try:
                    requests.post(self.cfg.plugin_api("play/config"),
                                  json={"enabled": True, "groups": groups,
                                        "callback": callback}, timeout=5)
                except requests.RequestException as e:
                    self.after(0, self._log, f"✗ 插件转发配置失败: {e}")
                    self.after(0, self._apply_state)
                    return
            err = self.engine.activate(rule_id, rule_name or f"玩法#{rule_id}", str(dest))
            if err:
                self.after(0, self._log, f"✗ 玩法激活失败: {err}")
            else:
                name = rule_name or f"玩法#{rule_id}"
                self._active_groups = set(groups)
                self._rp_build(rule_id, name)
                self.cfg.play_rule_id = rule_id
                self.cfg.play_rule_name = name
                self.cfg.play_enabled = True
                self.save_config(self.cfg)
                self.after(0, self._log,
                           f"✓ 复合玩法已自动激活（#{rule_id} {name}）"
                           + (f"｜ 游戏群 {','.join(groups)}" if groups
                              else "｜ 未配置游戏群：在玩法页勾选群后自动转发"))
                if groups and self.cfg.play_auto_register_members:
                    for gid in groups:
                        if not self._sync_members_quiet(client, gid):
                            break
            self.after(0, self._refresh_group_checkboxes)
            self.after(0, self._refresh_group_optmenu)
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)

        threading.Thread(target=run, name="play-autoload", daemon=True).start()

    def _reload_rule(self) -> None:
        """更新玩法文件：从总后台重新下载当前激活玩法（引擎 mtime 热重载生效）。"""
        if not self.engine.is_active():
            self._log("✗ 玩法未激活")
            return
        rid = int(self.engine.active_rule_id)

        def run() -> None:
            try:
                self._client().download_rule(rid, str(self.cfg.plays_dir() / f"rule_{rid}.py"))
                self.after(0, self._log, "✓ 玩法文件已更新（热重载自动生效）")
            except BackendError as e:
                self.after(0, self._log, f"✗ 下载玩法失败: {e}")

        threading.Thread(target=run, name="play-reload", daemon=True).start()

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
        """各游戏群本局状态总览行（UI 线程；本局有变化后调用）。"""
        if not hasattr(self, "lbl_rounds"):
            return  # 控件尚未构建完成时忽略
        lines = []
        if self.engine.is_active() and self.rp_game is not None:
            for gid in sorted(self._active_groups):
                if gid not in self.rp_game.announced:
                    continue
                info = self.engine.call_rule("seal_info", gid) or {}
                phase = info.get("phase") or "idle"
                rid = info.get("round_id") or ""
                ann = self.rp_game.announced[gid]
                fb = ann.get("first_bill")
                if phase == "betting":
                    tail = f"｜已下注 {info.get('n_bets')} 人" if int(info.get("n_bets") or 0) else ""
                    tail += f"｜首红包 {fb['total_num']} 份" if fb else ""
                    lines.append(f"群{gid}：{rid} 下注期{tail}")
                elif fb and int(fb.get("total_num") or 0) > 0:
                    st = "领完可结算" if ann.get("ready_to_settle") else \
                        f"已领 {fb.get('recv_num')}/{fb.get('total_num')}"
                    lines.append(f"群{gid}：{rid} 封盘｜{st}（红包不足自动作废）")
                else:
                    lines.append(f"群{gid}：{rid} 封盘｜等管理员发红包开奖")
        if lines:
            self.lbl_rounds.configure(text="\n".join(lines), text_color="#2ecc71")
        else:
            self.lbl_rounds.configure(text="", text_color="gray")
        for g, lbl in getattr(self, "_group_labels", {}).items():
            if self.engine.is_active() and g in self._active_groups:
                if g in (self.rp_game.announced if self.rp_game else {}):
                    lbl.configure(text="本局中", text_color="#2ecc71")
                else:
                    lbl.configure(text="待开局", text_color="gray")
            elif self.engine.is_active():
                lbl.configure(text="未参与", text_color="gray")
            else:
                lbl.configure(text="", text_color="gray")
        self._gate_buttons()

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
                      f"（多群各开各局：按钮与开奖表只作用于「当前群」）"),
                text_color="green")
        else:
            self.lbl_state.configure(text="未激活玩法（等待自动激活唯一复合玩法；勾选游戏群后自动转发）",
                                     text_color="gray")
        self._gate_buttons()
        self._refresh_group_optmenu()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy

    def _log_sep(self) -> None:
        """步骤分隔线：每个操作的一堆日志前加 ----------，方便观察。"""
        _flog("----------")
        try:
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", "----------\n")
            lines = int(self.txt_log.index("end-1c").split(".")[0])
            if lines > 500:
                self.txt_log.delete("1.0", f"{lines - 400}.0")
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        except Exception:
            pass

    def _log(self, text: str) -> None:
        _flog(text)
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
        建议关窗前「结算」或「重试未上报」；被封禁期间的残留局请先解封再结算。"""
        self._stop.set()
        try:
            if self._server:
                self._server.shutdown()
        except Exception:
            pass
        for t in (self._worker, self._server_thread):
            if t and t.is_alive():
                t.join(timeout=2)
