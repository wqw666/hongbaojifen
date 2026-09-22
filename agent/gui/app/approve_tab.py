"""第 4.5 个 Tab「积分审批」：群内上分/下分申请 → 审批 → 同步总后台。

链路：
  群成员发「上100」/「下100」（旧词「上分100/下分100」兼容）→ 插件（独立于玩法开关）
  POST 本机回调 /play/approve
  → play_tab 回调服务 → MainWindow 挂接 → 本页 push() 入队 → 表格展示「待审批」
  → 操作员选中点「通过」→ 调总后台 /api/open/points/up|down（带 executor_token，
  bizNo 幂等）→ 状态改为 已通过/已拒绝/失败(原因)

「申请时间」= 群里发出申请的时刻：新版插件转发时附带 QQ 消息发送时间（msg_time，
秒），据此格式化展示；旧插件不带时回退用 agent 收到时刻（转发即时，两者几乎一致）。

线程纪律：网络在 daemon 线程，UI 一律 after(0) 回主线程。
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Callable

import customtkinter as ctk
import requests

from integration.backend_client import BackendError, ExecutorBanned, OperatorDisabled
from .config_manager import config_dir

if False:  # pragma: no cover — 仅类型注释用
    from .config_manager import AppConfig


class ApprovalTab(ctk.CTkFrame):
    def __init__(self, master, cfg: "AppConfig", client_factory: Callable):
        super().__init__(master)
        self.cfg = cfg
        self.client_factory = client_factory  # () -> HbjfClient
        self._queue: queue.Queue = queue.Queue()       # HTTP 线程 → worker
        self._ui_queue: queue.Queue = queue.Queue()    # worker → UI 线程（轮询队列，禁止跨线程 after）
        self._rows: list[dict] = []
        self._stop = threading.Event()

        self._build_ui()
        self._restore_rows()
        self._worker = threading.Thread(target=self._loop, name="approve-worker", daemon=True)
        self._worker.start()
        # 主线程轮询器：跨线程 after() 会抛 RuntimeError('main thread is not in main loop')
        # （启动/弹窗/切视图等主线程不在 mainloop 的窗口期），异常又被 except 吞掉 → 申请静默丢失。
        # 改为纯 queue + 主线程 after 自续轮询，杜绝这条丢单路径。
        self.after(200, self._ui_poll)

    # ================= 界面 =================

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        ctk.CTkButton(row, text="通过", width=80, fg_color="#27ae60", hover_color="#1e8449",
                      command=self._approve_selected).pack(side="left", padx=4)
        ctk.CTkButton(row, text="拒绝", width=80, fg_color="#c0392b", hover_color="#a93226",
                      command=self._reject_selected).pack(side="left", padx=4)
        ctk.CTkButton(row, text="清空已处理", width=100, command=self._clear_done).pack(side="left", padx=4)
        self.lbl_info = ctk.CTkLabel(row, text="群成员发「上100/下100」即可在此看到申请",
                                     text_color="gray", font=ctk.CTkFont(size=11))
        self.lbl_info.pack(side="left", padx=10)

        import tkinter as tk
        from tkinter import ttk

        frame = tk.Frame(self, highlightthickness=1, highlightbackground="#CBD5E1")
        frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        cols = ("time", "gid", "qq", "nick", "action", "amount", "status")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c, w, t in [
            ("time", 150, "申请时间"),
            ("gid", 110, "群号"),
            ("qq", 110, "QQ"),
            ("nick", 130, "昵称"),
            ("action", 70, "类型"),
            ("amount", 80, "金额"),
            ("status", 200, "状态"),
        ]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("pending", foreground="#e67e22")
        self.tree.tag_configure("ok", foreground="#2ecc71")
        self.tree.tag_configure("rejected", foreground="#e74c3c")

    # ================= 事件入口（HTTP 线程 → 队列 → UI） =================

    def push(self, data: dict) -> None:
        """插件审批申请入队（HTTP 线程调用）。"""
        try:
            self._queue.put_nowait(data)
        except queue.Full:
            pass

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                row = self._build_row(data)
            except Exception:
                continue
            self._ui_queue.put(row)

    def _build_row(self, data: dict) -> dict:
        """worker 线程：纯数据构建，不碰任何 UI。"""
        action = "上分" if data.get("action") == "up" else "下分"
        ts = int(data.get("msg_time") or 0)
        # 申请时间：优先 QQ 消息真实发送时刻（插件 msg_time）；没有则用 agent 收到时刻
        applied_at = (datetime.fromtimestamp(ts) if ts > 0 else datetime.now())
        return {
            "time": applied_at.strftime("%m-%d %H:%M:%S"),
            "group_id": str(data.get("group_id") or ""),
            "qq": str(data.get("qq") or ""),
            "nickname": str(data.get("nickname") or ""),
            "action": data.get("action") or "up",
            "amount": int(data.get("amount") or 0),
            "status": "待审批",
            "biz_no": f"approve:{data.get('group_id')}:{data.get('qq')}:{data.get('amount')}:{int(datetime.now().timestamp())}",
        }

    def _ui_poll(self) -> None:
        """主线程：消费 worker 队列 → 入表（self.after 只在主线程调用，线程安全）。"""
        try:
            while True:
                row = self._ui_queue.get_nowait()
                self._insert_row(row)
        except queue.Empty:
            pass
        if not self._stop.is_set():
            self.after(200, self._ui_poll)

    def _insert_row(self, row: dict) -> None:
        if not row.get("amount") or not row.get("qq"):
            return  # 非法申请忽略
        self._rows.append(row)
        iid = f"{len(self._rows) - 1}"
        self.tree.insert("", "end", iid=iid, values=(
            row["time"], row["group_id"], row["qq"], row["nickname"],
            row["action"], row["amount"], row["status"]), tags=("pending",))
        self.lbl_info.configure(text=f"待审批 {self._pending_count()} 条（选中后点「通过/拒绝」）")
        self._persist()

    def _persist(self) -> None:
        """审批行落盘（%APPDATA%/QQHongbaoMonitor/approve_rows.json）：
        重启/闪退后待审批与已处理记录不丢——上分申请是钱，不能只活在内存里。"""
        try:
            (config_dir() / "approve_rows.json").write_text(
                json.dumps(self._rows, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _restore_rows(self) -> None:
        """启动时从磁盘恢复审批行（含已处理历史）。"""
        try:
            raw = json.loads((config_dir() / "approve_rows.json").read_text(encoding="utf-8"))
            rows = raw if isinstance(raw, list) else []
        except Exception:
            rows = []
        for r in rows:
            if not r.get("amount") or not r.get("qq"):
                continue
            self._rows.append(r)
            iid = f"{len(self._rows) - 1}"
            status = str(r.get("status") or "待审批")
            if status == "待审批":
                tags = ("pending",)
            elif status.startswith("已通过"):
                tags = ("ok",)
            else:
                tags = ("rejected",)
            self.tree.insert("", "end", iid=iid, values=(
                r.get("time") or "", r.get("group_id") or "", r.get("qq") or "",
                r.get("nickname") or "", r.get("action") or "up",
                r.get("amount") or 0, status), tags=tags)
        if self._rows:
            self.lbl_info.configure(text=f"待审批 {self._pending_count()} 条（选中后点「通过/拒绝」）")

    # ================= 审批动作 =================

    def _selected_rows(self) -> list[dict]:
        sels = [self.tree.item(i, "values") for i in self.tree.selection()]
        out = []
        for v in sels:
            for r in self._rows:
                if (r["time"] == v[0] and r["qq"] == v[2] and str(r["amount"]) == str(v[5])
                        and r["status"] == "待审批"):
                    out.append(r)
        return out

    def _pending_count(self) -> int:
        return sum(1 for r in self._rows if r["status"] == "待审批")

    def _approve_selected(self) -> None:
        self._process_selected(approve=True)

    def _reject_selected(self) -> None:
        self._process_selected(approve=False)

    def _process_selected(self, approve: bool) -> None:
        rows = self._selected_rows()
        if not rows:
            self.lbl_info.configure(text="请先选中「待审批」的申请行", text_color="#e67e22")
            return
        for r in rows:
            r["status"] = "处理中…"

        def work():
            for r in rows:
                client = self.client_factory()
                reason = f"群{r['group_id']} {'上分' if r['action'] == 'up' else '下分'}申请"
                try:
                    if approve:
                        fn = client.up_points if r["action"] == "up" else client.down_points
                        res = fn(r["qq"], r["amount"], reason=reason, biz_no=r["biz_no"],
                                 source="approve")
                        status = "已通过"
                        if res.get("duplicate"):
                            status = "已通过(重复单，未重复入账)"
                    else:
                        status = "已拒绝"
                    err = ""
                except (ExecutorBanned, OperatorDisabled) as e:
                    status, err = "失败(停摆)", str(e)
                except BackendError as e:
                    status, err = "失败", str(e)
                except Exception as e:  # noqa: BLE001
                    status, err = "失败", str(e)
                r["status"] = status
                r["err"] = err
                # 群里通知申请人结果（通过附带最新积分）
                self._notify_group(r)
                self.after(0, self._refresh_tree)
            self.after(0, lambda: self.lbl_info.configure(
                text=f"处理完成：通过 {sum(1 for r in rows if r['status'].startswith('已通过'))}，"
                     f"拒绝 {sum(1 for r in rows if r['status'] == '已拒绝')}，"
                     f"失败 {sum(1 for r in rows if r['status'].startswith('失败'))}"))
            self.after(0, self._persist)

        threading.Thread(target=work, daemon=True).start()

    def _balance_of(self, client, qq: str) -> int:
        """查最新积分（失败返回 0）。"""
        try:
            rows = client.query_points_batch([qq])
            if rows and rows[0].get("exists"):
                return int(rows[0].get("points") or 0)
        except Exception:
            pass
        return 0

    def _notify_group(self, r: dict) -> None:
        """审批结果群内通知：@申请人（通过附最新积分）。"""
        action = "上分" if r["action"] == "up" else "下分"
        if r["status"].startswith("已通过"):
            client = self.client_factory()
            text = f"{r['amount']}{action}申请已通过，当前积分为{self._balance_of(client, r['qq'])}"
        elif r["status"] == "已拒绝":
            text = f"{r['amount']}{action}申请已被拒绝"
        else:
            return  # 失败不入账，不通知（避免误导）
        try:
            requests.post(self.cfg.plugin_api("play/reply"),
                          json={"group_id": r["group_id"], "at_qq": r["qq"], "text": text},
                          timeout=5)
        except Exception:
            pass  # 通知失败不影响审批

    def _refresh_tree(self) -> None:
        for i, r in enumerate(self._rows):
            iid = str(i)
            if not self.tree.exists(iid):
                continue
            tag = "pending" if r["status"] == "待审批" else (
                "ok" if r["status"].startswith("已通过") else "rejected")
            status = r["status"] + (f"：{r.get('err')}" if r.get("err") else "")
            self.tree.item(iid, values=(
                r["time"], r["group_id"], r["qq"], r["nickname"],
                r["action"], r["amount"], status[:120]), tags=(tag,))

    def _clear_done(self) -> None:
        keep = [r for r in self._rows if r["status"] == "待审批" or r["status"] == "处理中…"]
        self._rows = keep
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for i, r in enumerate(self._rows):
            self.tree.insert("", "end", iid=str(i), values=(
                r["time"], r["group_id"], r["qq"], r["nickname"],
                r["action"], r["amount"], r["status"]), tags=("pending",))
        self.lbl_info.configure(text=f"待审批 {self._pending_count()} 条")
        self._persist()

    def shutdown(self) -> None:
        self._stop.set()
