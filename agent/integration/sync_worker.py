"""SyncWorker：执行器后台周期线程（daemon）。

每个 tick（默认 10s，见 cfg.heartbeat_interval_sec，最小 5s）：
1. 心跳 → /api/open/executor/heartbeat（token 无效立即报事件，不中断循环）
   心跳负载：host=本机名、version=程序版本、admin_qq=本机登录的管理员QQ（admin_qq_provider
   提供）—— 总后台据此自动注册操作员并记录最近登录时间/位置；执行器被封禁 → 40310 停摆
2. 拉取远程命令 → poll_commands，逐条本地执行并回报 done/failed
3. auto_sync_members 开启且配置了会员群时：每 ≥300s 自动跑一次会员同步

命令集 v1（命令必须幂等；sent 超过 5 分钟未回报总后台会重投一次）：
- get_status                 返回执行器状态
- set_config {k:v,...}      白名单键写入 cfg + save_config（+可选 plugin_config_sync）
- sync_members {group_id?}  同步会员群成员（缺省用 cfg.member_group_id）

事件回调 on_event(kind, payload) 在 worker 线程内触发：
  ("heartbeat", "ok" | 错误信息)  ("poll_error", 错误)  ("command", {id,command,status,message})
  ("banned", 封禁/停用说明)        ("sync", {total,added,existed,skipped,warning})
  ("fatal", 致命错误后线程退出)
GUI 侧务必用 after(0) 回 UI 线程处理。

封禁停摆：收到 40310（执行器被封禁）或 40311（绑定操作员被停用）→ 停摆：置 stop 退出循环，
不再心跳/poll/自动同步，等待总后台解封或更换 token 后由 GUI 重新「启动对接」。
worker 的 _run_sync 抛出的 ExecutorBanned 同样按停摆处理。

本模块不 import GUI；cfg 为 duck-typed 配置对象（有 heartbeat_interval_sec /
member_group_id / auto_sync_members / executor_name 等属性即可），成员来源用
provider 回调注入（见 member_sync.run_member_sync）。
"""
from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime

from .backend_client import BackendError, ExecutorBanned, HbjfClient, OperatorDisabled
from . import member_sync

MIN_INTERVAL = 5          # 心跳最小间隔秒
MIN_AUTO_SYNC_GAP = 300   # 自动同步最小间隔秒（避免每次心跳都打全量同步）
CONFIG_WHITELIST = ("heartbeat_interval_sec", "member_group_id", "auto_sync_members")


class SyncWorker(threading.Thread):
    def __init__(self, client: HbjfClient, cfg, save_config=None, on_event=None,
                 members_provider=None, group_provider=None, plugin_config_sync=None,
                 admin_qq_provider=None, version: str = ""):
        super().__init__(name="sync-worker", daemon=True)
        self.client = client
        self.cfg = cfg
        self.save_config = save_config
        self.on_event = on_event
        self.members_provider = members_provider
        self.group_provider = group_provider
        self.plugin_config_sync = plugin_config_sync
        # admin_qq_provider() -> {"qq":..., "nickname":...} | None；本机登录的管理员QQ
        self.admin_qq_provider = admin_qq_provider
        self.version = version
        self._hostname = socket.gethostname() or ""
        self._stop = threading.Event()
        self._last_auto_sync = 0.0
        self.alive = False
        self.banned = False  # True=已因封禁/停用停摆

    # ---------- 对外控制 ----------

    def start_worker(self) -> None:
        self._stop.clear()
        self.banned = False
        self.alive = True
        self.start()

    def stop_worker(self, join_sec: float = 2.0) -> None:
        """请求停止并最多等待 join_sec 秒（关窗时调用，确保无残留线程）。"""
        self.alive = False
        self._stop.set()
        if self.is_alive():
            self.join(join_sec)

    # ---------- 主循环 ----------

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except (ExecutorBanned, OperatorDisabled) as e:
                self.banned = True
                self._emit("banned", str(e))
                break
            except Exception as e:  # 兜底：单 tick 异常不杀死线程
                self._emit("fatal", f"tick 异常：{e}")
                break
            self._stop.wait(self._interval())

    def _interval(self) -> int:
        try:
            return max(MIN_INTERVAL, int(getattr(self.cfg, "heartbeat_interval_sec", 10)))
        except (TypeError, ValueError):
            return 10

    def _tick(self) -> None:
        # 1) 心跳（带本机登录的管理员QQ → 总后台自动注册/续活操作员）
        admin = self._current_admin()
        try:
            self.client.heartbeat(version=self.version, host=self._hostname,
                                  admin_qq=admin.get("qq", "") if admin else "",
                                  admin_nickname=admin.get("nickname", "") if admin else "")
            self._emit("heartbeat", "ok")
        except (ExecutorBanned, OperatorDisabled) as e:
            raise
        except BackendError as e:
            self._emit("heartbeat", str(e))
            return  # 连不上总后台就不 poll，下个 tick 再试

        # 2) 远程命令
        try:
            cmds = self.client.poll_commands()
        except BackendError as e:
            self._emit("poll_error", str(e))
            return
        for cmd in cmds:
            status, message = "failed", ""
            try:
                status, message = self._execute(cmd)
            except Exception as e:  # 命令执行异常按 failed 回报，由总后台记录
                status, message = "failed", f"本地执行异常：{e}"
            try:
                self.client.report_command_result(cmd["id"], status, message or "ok")
            except BackendError as e:
                self._emit("command", {"id": cmd.get("id"), "command": cmd.get("command"),
                                       "status": status, "message": message,
                                       "report_error": str(e)})
                continue
            self._emit("command", {"id": cmd.get("id"), "command": cmd.get("command"),
                                   "status": status, "message": message})

        # 3) 自动会员同步（低频）
        if getattr(self.cfg, "auto_sync_members", False) and self.members_provider:
            gid = str(getattr(self.cfg, "member_group_id", "") or "").strip()
            if gid and time.time() - self._last_auto_sync >= MIN_AUTO_SYNC_GAP:
                self._last_auto_sync = time.time()
                self._run_sync(gid)

    def _current_admin(self) -> dict | None:
        """取本机登录的管理员QQ（静默失败返回 None → 心跳不带 admin 字段）。"""
        if not self.admin_qq_provider:
            return None
        try:
            info = self.admin_qq_provider() or {}
            qq = str(info.get("qq") or info.get("uin") or "").strip()
            if not qq or not qq.isdigit():
                return None
            return {"qq": qq, "nickname": str(info.get("nickname") or "")}
        except Exception:
            return None

    # ---------- 命令执行 ----------

    def _execute(self, cmd: dict) -> tuple[str, str]:
        command = str(cmd.get("command") or "")
        params = self._parse_params(cmd.get("params"))
        if command == "get_status":
            return "done", json.dumps(self._status_snapshot(), ensure_ascii=False)
        if command == "set_config":
            return self._apply_config(params)
        if command == "sync_members":
            gid = str((params or {}).get("group_id") or getattr(self.cfg, "member_group_id", "") or "").strip()
            if not gid or not self.members_provider:
                return "failed", "未配置会员群或成员来源不可用"
            result = self._run_sync(gid)
            return "done", json.dumps(result, ensure_ascii=False)
        return "failed", f"未知命令: {command}"

    def _apply_config(self, params: dict) -> tuple[str, str]:
        if not isinstance(params, dict):
            return "failed", f"set_config 参数须为 JSON 对象，收到: {params!r}"
        changed, ignored = [], []
        for key, value in params.items():
            if key not in CONFIG_WHITELIST:
                ignored.append(key)
                continue
            if key == "heartbeat_interval_sec":
                try:
                    value = max(MIN_INTERVAL, int(value))
                except (TypeError, ValueError):
                    ignored.append(key)
                    continue
            if key == "auto_sync_members":
                value = bool(value)
            if not hasattr(self.cfg, key):
                ignored.append(key)
                continue
            if getattr(self.cfg, key) != value:
                setattr(self.cfg, key, value)
                changed.append(key)
        note = ""
        if ignored:
            note = f"，已忽略不支持键: {','.join(ignored)}"
        if changed and self.save_config:
            try:
                self.save_config()
            except Exception as e:
                return "failed", f"配置已改但保存失败: {e}"
        if self.plugin_config_sync:
            try:
                self.plugin_config_sync()
            except Exception as e:
                note += f"，插件配置同步失败: {e}"
        if not changed and not ignored:
            return "done", "配置无变化"
        return "done", ("已更新: " + ",".join(changed) if changed else "未更新") + note

    def _run_sync(self, group_id: str) -> dict:
        result = member_sync.run_member_sync(
            self.client, group_id, self.members_provider, self.group_provider,
            on_progress=lambda msg: self._emit("sync_progress", msg))
        self._emit("sync", result)
        return result

    def _status_snapshot(self) -> dict:
        return {
            "status": "banned" if self.banned else "online",
            "name": str(getattr(self.cfg, "executor_name", "") or ""),
            "version": self.version,
            "host": self._hostname,
            "member_group_id": str(getattr(self.cfg, "member_group_id", "") or ""),
            "auto_sync_members": bool(getattr(self.cfg, "auto_sync_members", False)),
            "heartbeat_interval_sec": self._interval(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _parse_params(raw) -> dict | None:
        if raw is None or raw == "":
            return None
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw  # 非 JSON 原样返回，由各命令校验

    def _emit(self, kind: str, payload) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass  # 事件回调异常不影响 worker
