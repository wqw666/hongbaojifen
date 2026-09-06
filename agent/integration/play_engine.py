"""玩法规则引擎：加载总后台下发的玩法 .py 文件，把群消息交给玩法处理并得到回复与积分变动。

玩法文件协议 v2（上传到总后台的玩法文件必须遵守；v1 单返回兼容）：
    def handle_message(group_id: int, qq: int, nickname: str, text: str)
        -> str | None                        # v1：None=不回复；str=回复文本（发送侧自动 @）
        -> dict {"reply": str|None, "delta": int}   # v2：reply 同上；delta=该消息的积分变动
        -> (reply, delta)                    # v2 元组写法
其中 delta>0 玩家加分、delta<0 玩家扣分、0=只记过程不加分；None=本条纯互动不回复。
引擎每次调用后把积分变动放在 engine.last_delta（单消息 worker 串行，线程安全）；
群消息 → 玩法 → 回复/积分落进 RoundSession 当前局，操作员「结算」后整局上报总后台入账。

玩法可选协议（按需定义，未定义即忽略）：
    def handle_redpacket(group_id, qq, nickname, amount)         # 红包领取事件（返回协议同 handle_message）
    def settle_redpacket(group_id, claims, rate_permille=20)     # 红包领完批量结算（大吃小等复杂玩法）
    def handle_round_start(group_id)                             # GUI「开始本局」：开局重置玩法状态
    def handle_round_end(group_id)                               # GUI「结束本局」/自动收尾：清理玩法状态
    def handle_round_abort(group_id)                             # 下注期点「结束本局」：返回终止公告文本或 None
其中 handle_round_start/end 无返回值要求；引擎在操作员点「开始本局/结束本局」或
红包领完自动收尾时调用。需要"整局状态"的玩法（如大吃小需要开局进入下注期）应在此重置。
handle_round_abort 由引擎在「结束本局」进入结算收尾**前**问询玩法（大吃小在下注期点
结束本局 = 提前终止）：返回非空文本 → agent 按「本局已终止（积分已退还，不抽水）」
只 @全体 播报并作废本局、不上报；返回 None → 按正常结算上报收尾。

约定：
- 玩法文件应快速返回，不要做阻塞 IO/长循环（引擎在 GUI 的消息 worker 线程里逐个调用）
- 引擎 importlib 动态加载；文件 mtime 变化时自动重新加载（总后台重传 → agent 重新下载即热生效）
- 玩法代码抛异常只记 last_error 并放弃本条回复，不崩引擎
- 同 qq 1 秒内的重复发言直接忽略（防刷屏风暴；玩法想要更高频需自行实现）
- 需要“整局状态”（当前雷号/本局名单等）的玩法用模块级变量自己记；文件热重载会清掉旧状态

纯 requests/标准库，不依赖 GUI，可独立测试：python -m integration.play_engine
"""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .backend_client import BackendError, ExecutorBanned, OperatorDisabled, HbjfClient

DEDUP_WINDOW_SEC = 1.0  # 同一 qq 发言去重窗口


class RuleEngine:
    """持有"当前激活玩法"的单例逻辑。线程安全（GUI worker 串行调用时锁不竞争）。"""

    def __init__(self, rules_dir: str | Path = ""):
        self.rules_dir = Path(rules_dir) if rules_dir else Path.cwd()
        self._lock = threading.Lock()
        self._modules: dict[int, object] = {}   # rule_id -> 已加载模块
        self._paths: dict[int, Path] = {}       # rule_id -> 玩法文件路径（热重载用）
        self._mtimes: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}  # qq -> 上次发言时间戳（去重）
        self.active_rule_id: int | None = None
        self.active_rule_name: str = ""
        self.last_error: str = ""               # 最近一次加载失败/调用异常描述
        self.last_delta: int = 0                # 最近一次 handle_message 的积分变动（v2，默认 0）

    # ---------- 状态 ----------

    def is_active(self) -> bool:
        return self.active_rule_id is not None

    def rule_summary(self) -> str:
        """如「rule_add1@1.0」；未启用返回空串。"""
        if not self.is_active():
            return ""
        return self.active_rule_name or f"rule#{self.active_rule_id}"

    # ---------- 加载 / 启停 ----------

    def activate(self, rule_id: int | str, rule_name: str, file_path: str | Path) -> str | None:
        """试加载玩法文件，成功则激活。返回 None=成功；返回错误文本=失败（不激活）。"""
        path = Path(file_path)
        if not path.is_file():
            return f"玩法文件不存在: {path}"
        err = self._load_module(rule_id, path)
        if err is not None:
            return err
        with self._lock:
            self.active_rule_id = int(rule_id)
            self.active_rule_name = rule_name
            self.last_error = ""
        return None

    def deactivate(self) -> None:
        with self._lock:
            self.active_rule_id = None
            self.active_rule_name = ""
            self._modules.clear()
            self._paths.clear()
            self._mtimes.clear()

    def _module_name(self, rule_id: int, path: Path) -> str:
        stem = re.sub(r"[^A-Za-z0-9_]", "_", path.stem)
        return f"_play_rule_{rule_id}_{stem or 'file'}"

    def _import_module(self, rule_id: int, path: Path):
        mod_name = self._module_name(rule_id, path)
        sys.modules.pop(mod_name, None)  # 丢弃旧模块（含其里可能注册的全局状态）
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法解析 {path.name} 为 Python 模块")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        return module

    def _load_module(self, rule_id: int | str, path: Path) -> str | None:
        rid = int(rule_id)
        try:
            module = self._import_module(rid, path)
            if not callable(getattr(module, "handle_message", None)):
                return (f"玩法 {path.name} 缺少入口函数: handle_message(group_id, qq, nickname, text)")
        except Exception as e:  # noqa: BLE001 — 文件内容错误要转成可读信息
            return f"玩法 {path.name} 加载失败: {e.__class__.__name__}: {e}"
        with self._lock:
            self._modules[rid] = module
            self._paths[rid] = path
            self._mtimes[rid] = self._file_mtime(path)
            self.last_error = ""
        return None

    @staticmethod
    def _file_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    # ---------- 消息处理 ----------

    def handle_message(self, group_id: int | str, qq: int | str, nickname: str, text: str) -> str | None:
        """按激活玩法处理一条群消息。返回回复文本（发送侧自动 @），None=不回复。
        玩法文件 v2 可返回 dict/tuple 携带积分变动，落在 self.last_delta 上。"""
        self.last_delta = 0
        with self._lock:
            if self.active_rule_id is None:
                return None
            # 同 qq 去重窗口
            now = time.time()
            if now - self._last_seen.get(int(qq), 0.0) < DEDUP_WINDOW_SEC:
                return None
            self._last_seen[int(qq)] = now
            rid = self.active_rule_id
            module = self._modules.get(rid)
            path = self._paths.get(rid)
            if module is None or path is None:
                return None
            # 热重载：文件变了就重新加载（加载失败保留旧模块并记录错误）
            mtime = self._file_mtime(path)
            if mtime != self._mtimes.get(rid):
                try:
                    module = self._import_module(rid, path)
                    self._modules[rid] = module
                    self._mtimes[rid] = mtime
                    self.last_error = ""
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"玩法热重载失败（沿用旧版）: {e}"
        # 锁外执行玩法代码，避免玩家代码持锁卡死其他调用
        try:
            fn = getattr(module, "handle_message")
            raw = fn(int(group_id), int(qq), str(nickname), str(text))
            reply, delta = self._parse_result(raw)
            self.last_delta = delta
            if reply is None:
                return None
            reply = reply.strip()
            return reply or None
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法执行异常: {e.__class__.__name__}: {e}"
            return None

    @staticmethod
    def _parse_result(raw) -> tuple[str | None, int]:
        """玩法返回值 → (回复文本|None, 积分变动)。v1 str/None；v2 dict {"reply","delta"} 或 (reply, delta)。"""
        if isinstance(raw, dict):
            delta_raw = raw.get("delta", 0)
            return (str(raw["reply"]).strip() if raw.get("reply") is not None else None,
                    _to_int(delta_raw))
        if isinstance(raw, (tuple, list)):
            if len(raw) < 2:
                raise ValueError(f"v2 元组返回值需 (reply, delta) 两项: {raw!r}")
            reply, delta = raw[0], raw[1]
            return (str(reply).strip() if reply is not None else None, _to_int(delta))
        if raw is None:
            return None, 0
        return str(raw), 0

    # ---------- 红包领取处理（可选协议，玩法文件可定义 handle_redpacket） ----------

    def has_redpacket_handler(self) -> bool:
        """当前激活玩法是否定义了 handle_redpacket（决定红包计分玩法是否启用）。"""
        with self._lock:
            if self.active_rule_id is None:
                return False
            m = self._modules.get(self.active_rule_id)
            return bool(m and callable(getattr(m, "handle_redpacket", None)))

    def handle_redpacket_batch(self, group_id: int | str,
                                claims: list[dict], rate_permille: int = 20) -> dict | None:
        """红包领完时的批量结算（玩法4 大吃小等复杂玩法用）。
        玩法文件可定义 settle_redpacket(group_id, claims, rate_permille=20)，返回
        {"events": [{qq, nickname, reply, delta}...], "announce": str} 或 None。
        claims 元素: {qq, nickname, amount}（含红包领取明细，金额为元）。"""
        with self._lock:
            if self.active_rule_id is None:
                return None
            rid = self.active_rule_id
            module = self._modules.get(rid)
            path = self._paths.get(rid)
            if module is None or path is None:
                return None
            mtime = self._file_mtime(path)
            if mtime != self._mtimes.get(rid):
                try:
                    module = self._import_module(rid, path)
                    self._modules[rid] = module
                    self._mtimes[rid] = mtime
                    self.last_error = ""
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"玩法热重载失败（沿用旧版）: {e}"
        fn = getattr(module, "settle_redpacket", None)
        if not callable(fn):
            return None
        try:
            import inspect as _inspect
            params = _inspect.signature(fn).parameters
            if len(params) >= 3:
                raw = fn(int(group_id), list(claims or []), int(rate_permille or 20))
            elif len(params) == 2:
                raw = fn(int(group_id), list(claims or []))
            else:
                raw = fn(int(group_id), list(claims or []), int(rate_permille or 20))
            if isinstance(raw, dict):
                return raw
            return None
        except Exception as e:  # noqa: BLE001
            self.last_error = f"玩法红包结算异常: {e.__class__.__name__}: {e}"
            return None

    def handle_redpacket(self, group_id: int | str, qq: int | str, nickname: str,
                         amount: float) -> tuple[str | None, int]:
        """按激活玩法处理一条红包领取事件。返回 (回复文本|None, 积分变动)。
        玩法文件可选定义 handle_redpacket(group_id, qq, nickname, amount)，返回协议同
        handle_message（None / str / dict / tuple）；未定义或未激活返回 (None, 0)。
        不做同 qq 去重（红包去重由 RedPacketGame 按单号+QQ 幂等处理）。"""
        self.last_delta = 0
        with self._lock:
            if self.active_rule_id is None:
                return None, 0
            rid = self.active_rule_id
            module = self._modules.get(rid)
            path = self._paths.get(rid)
            if module is None or path is None:
                return None, 0
            # 热重载：文件变了就重新加载（与 handle_message 一致）
            mtime = self._file_mtime(path)
            if mtime != self._mtimes.get(rid):
                try:
                    module = self._import_module(rid, path)
                    self._modules[rid] = module
                    self._mtimes[rid] = mtime
                    self.last_error = ""
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"玩法热重载失败（沿用旧版）: {e}"
        fn = getattr(module, "handle_redpacket", None)
        if not callable(fn):
            return None, 0
        try:
            raw = fn(int(group_id), int(qq), str(nickname), float(amount or 0))
            reply, delta = self._parse_result(raw)
            self.last_delta = delta
            if reply is None:
                return None, delta
            reply = reply.strip()
            return reply or None, delta
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法红包处理异常: {e.__class__.__name__}: {e}"
            return None, 0

    # ---------- 局生命周期通知（GUI「开始本局/结束本局」等） ----------

    def notify_round_start(self, group_id: int | str, round_id: str = "") -> None:
        """「开始本局」→ 玩法可选 handle_round_start(group_id, round_id)：开局进入可玩状态（下注期等）。

        round_id 为本局局号（GUI 生成，如 hongbaojifen_00000001），玩法若要回复「本局 局号」需
        收下并保存；玩法函数只声明 (group_id) 一个参数时兼容调用（不传局号）。
        无返回值；玩法未定义该函数或未激活时为 no-op。调用失败只记 last_error。"""
        self._notify_optional("handle_round_start", group_id, round_id)

    def notify_round_end(self, group_id: int | str) -> None:
        """「结束本局」/红包领完自动收尾 → 玩法可选 handle_round_end(group_id)：清理整局状态。

        防止把上一局的状态（下注名单/开奖期）泄漏到下一局。"""
        self._notify_optional("handle_round_end", group_id)

    def rule_bettors(self, group_id: int | str) -> set[str]:
        """玩法可选 bettor_qqs(group_id) → 本局已下注者 QQ 集合（str）。

        供红包领完判定「下注者均已领取」提前收尾（大吃小等玩法实现）；玩法未提供、
        未激活或调用异常都返回空集（此时只按红包全领完收尾）。"""
        module = self._current_module()
        if module is None:
            return set()
        fn = getattr(module, "bettor_qqs", None)
        if not callable(fn):
            return set()
        try:
            raw = fn(int(group_id))
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法bettor_qqs异常: {e.__class__.__name__}: {e}"
            return set()
        if not raw:
            return set()
        return {str(q) for q in raw if str(q)}

    def query_round_abort(self, group_id: int | str) -> str | None:
        """玩法可选 handle_round_abort(group_id) → 终止公告文本（str）或 None。

        「结束本局」在下注期被点下、尚未进入收尾结算前，引擎先问玩法能否按「提前终止
        （积分已退还，不抽水）」收尾：玩法返回文本 → agent 只 @全体 播报并作废本局、
        不上报；返回 None → 走正常结算上报。玩法未提供/未激活/调用异常都返回 None
        （此时按正常收尾处理，玩法 bug 只记 last_error）。"""
        module = self._current_module()
        if module is None:
            return None
        fn = getattr(module, "handle_round_abort", None)
        if not callable(fn):
            return None
        try:
            return fn(int(group_id))
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法handle_round_abort异常: {e.__class__.__name__}: {e}"
            return None

    def _notify_optional(self, fn_name: str, group_id: int | str, extra: str = "") -> None:
        """取当前激活玩法模块（含热重载检查），有可选函数 fn_name 就调用。

        extra 为附加上下文（如局号 round_id）：玩法函数声明 2 个参数且 extra 非空时
        以 fn(group_id, extra) 调用，否则只传 group_id（兼容旧写法）。"""
        module = self._current_module()
        if module is None:
            return
        fn = getattr(module, fn_name, None)
        if not callable(fn):
            return
        try:
            if extra:
                import inspect as _inspect
                if len(_inspect.signature(fn).parameters) >= 2:
                    fn(int(group_id), str(extra))
                    return
            fn(int(group_id))
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法{fn_name}异常: {e.__class__.__name__}: {e}"

    def _current_module(self) -> object | None:
        """锁内取当前激活玩法模块（文件变了自动热重载）；未激活/无模块返回 None。

        供局生命周期通知等"调用后丢弃返回值"的入口使用；取到模块后在锁外执行玩法代码。"""
        with self._lock:
            if self.active_rule_id is None:
                return None
            rid = self.active_rule_id
            module = self._modules.get(rid)
            path = self._paths.get(rid)
            if module is None or path is None:
                return None
            # 热重载：文件变了就重新加载（与 handle_message 一致；失败沿用旧模块并记录错误）
            mtime = self._file_mtime(path)
            if mtime != self._mtimes.get(rid):
                try:
                    module = self._import_module(rid, path)
                    self._modules[rid] = module
                    self._mtimes[rid] = mtime
                    self.last_error = ""
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"玩法热重载失败（沿用旧版）: {e}"
            return module


def _to_int(value) -> int:
    """宽松整型转换（int / 数字串 / float），失败回 0。积分变动给非法值当 0 处理。"""
    try:
        if isinstance(value, bool):
            return 0
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0


# ---------------------------------------------------------------------------
# RoundSession：一局游戏的缓冲与结算上报（多游戏群各自一局）
# ---------------------------------------------------------------------------

# 单局缓冲：达到 soft 上限时自动结算一次；hard 上限是硬顶（=总后台单局事件上限）
FLUSH_LIMIT = 1000
HARD_LIMIT = 2000


class RoundSession:
    """每个游戏群维护一个“当前局”：玩法引擎算出的 (回复, delta) 逐个落进 events；
    操作员「结算」或事件数到 soft 上限时把整局上报总后台（round_id 幂等）。
    上报成功才开新局；失败保留事件可重试（执行器被封禁 → ExecutorBanned 上抛，调用方停摆）。
    线程安全：事件添加来自消息 worker 线程，结算来自 GUI 按钮线程，内部用锁串行。"""

    def __init__(self, client: HbjfClient, play_name: str = "", play_id: int | str = ""):
        self.client = client
        self.play_name = play_name
        self.play_id = play_id
        self._lock = threading.RLock()
        self._rounds: dict[str, dict] = {}  # group_id -> {group_id, round_id, label, started_at, events}
        self._inflight: set[str] = set()    # 正在上报的群（防重复结算/并发 flush）

    # ---------- 事件 ----------

    def add_event(self, group_id: str | int, qq: str | int, nickname: str,
                  msg: str, reply: str, delta: int) -> dict:
        """把一条游戏消息事件放进该群当前局。返回信息供界面日志：
        {group_id, round_id, label, event_count, total_delta, dropped}
        dropped=True = 单局缓冲已满（>HARD_LIMIT 条）本条未入局，仅提示。"""
        gid = str(group_id)
        with self._lock:
            r = self._rounds.get(gid)
            if r is None:
                r = self._open(gid)
            if len(r["events"]) >= HARD_LIMIT:
                return self._info(r, dropped=True)
            r["events"].append({"qq": str(qq), "nickname": str(nickname or ""),
                                "msg": str(msg or ""), "reply": str(reply or ""),
                                "delta": _to_int(delta),
                                "ts": datetime.now().strftime("%m-%d %H:%M:%S")})
            return self._info(r)

    def _open(self, group_id: str) -> dict:
        now = datetime.now()
        r = {"group_id": group_id,
             "round_id": f"G{group_id[-6:]}-{now.strftime('%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}",
             "label": now.strftime("%H:%M:%S"),
             "started_at": now.strftime("%Y-%m-%d %H:%M:%S"),
             "events": []}
        self._rounds[group_id] = r
        return r

    def round_info(self, group_id: str | int) -> dict | None:
        with self._lock:
            r = self._rounds.get(str(group_id))
            return self._info(r) if r else None

    def all_rounds(self) -> list[dict]:
        """所有群的当前局概要（无事件的空局不返回）。"""
        with self._lock:
            return [self._info(r) for r in self._rounds.values() if r["events"]]

    def _info(self, r: dict, dropped: bool = False) -> dict:
        return {"group_id": r["group_id"], "round_id": r["round_id"], "label": r["label"],
                "event_count": len(r["events"]),
                "total_delta": sum(int(e["delta"]) for e in r["events"]),
                "dropped": dropped}

    # ---------- 结算上报 ----------

    def settle(self, group_id: str | int) -> dict:
        """上报该群当前局并（成功后）开新局。成功返回结果 map（server=总后台校验明细）；
        {busy: True} = 该局已在别的线程上报中；{empty: True} = 当前局无事件。
        网络/封禁失败原样抛 BackendError 子类（事件保留可重试）。"""
        gid = str(group_id)
        with self._lock:
            r = self._rounds.get(gid)
            if r is None or not r["events"]:
                return {"group_id": gid, "empty": True}
            if gid in self._inflight:
                return {"group_id": gid, "busy": True}
            self._inflight.add(gid)
            snapshot = [{"qq": e["qq"], "nickname": e["nickname"], "msg": e["msg"],
                         "reply": e["reply"], "delta": e["delta"], "ts": e.get("ts", "")}
                        for e in r["events"]]
            round_id, label, started_at = r["round_id"], r["label"], r["started_at"]
        try:
            data = self.client.report_game_round(
                round_id=round_id, play_name=self.play_name, play_id=self.play_id,
                group_id=gid, events=snapshot)
        except BackendError:
            raise
        finally:
            with self._lock:
                self._inflight.discard(gid)
        with self._lock:
            # 上报成功才清事件开新局；失败事件保留可重试
            cur = self._rounds.get(gid)
            if cur is not None and cur["round_id"] == round_id and len(cur["events"]) == len(snapshot):
                self._open(gid)
        return {"group_id": gid, "round_id": round_id, "label": label,
                "started_at": started_at, "duplicate": bool(data.get("duplicate")),
                "server": data}

    def settle_all(self) -> list[dict]:
        """全部有事件的群各结算一次。逐个 try/except：单个失败不影响其它群。"""
        results = []
        for gid in self.pending_groups():
            try:
                results.append(self.settle(gid))
            except BackendError as e:
                results.append({"group_id": gid, "error": str(e)})
        return results

    def pending_groups(self) -> list[str]:
        with self._lock:
            return [gid for gid, r in self._rounds.items() if r["events"]]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._rounds.values() if r["events"])


# ---------------------------------------------------------------------------
# 自测：python -m integration.play_engine
# ---------------------------------------------------------------------------

_SAMPLE_ADD1 = '''"""测试玩法 1：收到纯数字 -> 回复数字+1；否则不回复。"""
def handle_message(group_id, qq, nickname, text):
    t = text.strip()
    if not t.isdigit():
        return None
    return str(int(t) + 1)
'''

_SAMPLE_ADD2 = '''"""测试玩法 2：收到纯数字 -> 回复数字+2；非数字 -> 提示。"""
def handle_message(group_id, qq, nickname, text):
    t = text.strip()
    if not t.isdigit():
        return "请输入数字~"
    return str(int(t) + 2)
'''


_SAMPLE_V2 = '''"""v2 计分玩法样本：返回 dict/tuple 带积分变动。"""
def handle_message(group_id, qq, nickname, text):
    t = text.strip()
    if t == "中雷":
        return {"reply": "命中雷 -10", "delta": -10}
    if t == "答对":
        return ("答对 +1", 1)
    if t == "白说":
        return {"reply": "", "delta": 0}
    return None
'''


class _FakeBackend:
    """selftest 用假总后台：记录上报、可注入失败/封禁。"""

    def __init__(self) -> None:
        self.reports: list[dict] = []
        self.fail = False
        self.banned = False

    def report_game_round(self, round_id: str, play_name: str, group_id: str,
                          events: list[dict], play_id: int | str = "",
                          executor_token: str | None = None) -> dict:
        if self.banned:
            raise ExecutorBanned("执行器已被封禁")
        if self.fail:
            raise BackendError("网络失败")
        self.reports.append({"round_id": round_id, "play_name": play_name,
                             "group_id": group_id, "events": events})
        return {"round_id": round_id, "duplicate": False, "member_count": len(
            {e["qq"] for e in events}), "total_delta": sum(int(e["delta"]) for e in events),
            "event_count": len(events), "warning_count": 0, "warning": ""}


def _selftest() -> int:
    import tempfile
    from pathlib import Path

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    tmp = Path(tempfile.mkdtemp(prefix="play_engine_selftest_"))
    # 优先用仓库 agent/play_rules/ 里的真实样本，找不到就内联
    samples = Path(__file__).resolve().parent.parent / "play_rules"
    ok = True

    def step(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= cond
        print(f"  {'✓' if cond else '✗'} {name}" + (f" — {detail}" if detail else ""))

    # 准备两个玩法文件
    files: dict[str, Path] = {}
    for stem, fallback in (("rule_add1", _SAMPLE_ADD1), ("rule_add2", _SAMPLE_ADD2)):
        src = samples / f"{stem}.py"
        dst = tmp / f"{stem}.py"
        if src.is_file():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            step(f"样本 {stem}.py 从仓库 play_rules/ 取用", True)
        else:
            dst.write_text(fallback, encoding="utf-8")
            step(f"样本 {stem}.py 回退内联（仓库 play_rules/ 不存在）", True)
        files[stem] = dst

    eng = RuleEngine(rules_dir=tmp)

    # 玩法 1
    err = eng.activate(1, "rule_add1@1.0", files["rule_add1"])
    step("激活 rule_add1", err is None, err or "")
    step("rule_add1 数字+1", eng.handle_message(10001, 1001, "小明", "1") == "2", "发1→回2")
    step("rule_add1 大数字", eng.handle_message(10001, 1005, "小刚", "99") == "100")
    step("rule_add1 非数字不回复", eng.handle_message(10001, 1002, "小红", "你好") is None)
    step("rule_add1 空文本不回复", eng.handle_message(10001, 1002, "小红", " ") is None)
    step("rule_add1 同qq秒内去重", eng.handle_message(10001, 1003, "小刚", "5") == "6"
         and eng.handle_message(10001, 1003, "小刚", "7") is None, "第二条7被去重")

    # 热重载：等 mtime 变化后改文件语义
    time.sleep(0.02)
    f1 = files["rule_add1"]
    f1.write_text(f1.read_text(encoding="utf-8").replace("+ 1", "+ 10"), encoding="utf-8")
    os_utime = __import__("os").utime
    os_utime(f1, None)
    time.sleep(0.05)  # 确保 mtime 改变被 stat 察觉
    time.sleep(1.1)  # 绕过去重窗口
    step("rule_add1 热重载 +10 生效", eng.handle_message(10001, 1004, "小强", "1") == "11", "改文件后发1→回11")

    # 玩法 2（+2 校验）
    err = eng.activate(2, "rule_add2@1.0", files["rule_add2"])
    step("激活 rule_add2", err is None, err or "")
    step("rule_add2 数字+2", eng.handle_message(10001, 2001, "阿花", "1") == "3", "发1→回3")
    step("rule_add2 非数字提示", eng.handle_message(10001, 2002, "阿狗", "abc") == "请输入数字~")

    # 加载失败路径
    bad = tmp / "bad.py"
    bad.write_text("def handle_message(:\n", encoding="utf-8")
    err = eng.activate(99, "bad", bad)
    step("坏文件加载报错不激活", err is not None and eng.active_rule_id != 99, err or "")

    # 缺入口路径
    nofn = tmp / "nofn.py"
    nofn.write_text("x = 1\n", encoding="utf-8")
    err = eng.activate(98, "nofn", nofn)
    step("缺 handle_message 报错", err is not None and "handle_message" in (err or ""), err or "")

    eng.deactivate()
    step("deactivate 后不回复", eng.handle_message(10001, 1, "x", "1") is None)

    # ---- v2 计分协议：dict / tuple 返回 ----
    eng2 = RuleEngine(rules_dir=tmp)
    v2 = tmp / "rule_v2.py"
    v2.write_text(_SAMPLE_V2, encoding="utf-8")
    err = eng2.activate(7, "v2demo@1.0", v2)
    step("v2 激活", err is None, err or "")
    r = eng2.handle_message(10001, 7001, "小红", "中雷")
    step("v2 dict 回复+扣分", r == "命中雷 -10" and eng2.last_delta == -10,
         f"reply={r} delta={eng2.last_delta}")
    r = eng2.handle_message(10001, 7002, "小蓝", "答对")
    step("v2 tuple 回复+加分", r == "答对 +1" and eng2.last_delta == 1,
         f"reply={r} delta={eng2.last_delta}")
    step("v2 空回复不加分", eng2.handle_message(10001, 7003, "小绿", "白说") is None
         and eng2.last_delta == 0)
    step("v2 忽略发言", eng2.handle_message(10001, 7004, "小紫", "随便聊聊") is None
         and eng2.last_delta == 0, f"delta={eng2.last_delta}")
    err = eng2.activate(8, "rule_add1@1.0", files["rule_add1"])
    # 注：该文件先前被热重载用例改写为 +10，回复应为 11；只关心 v1 玩法 delta 恒 0
    step("v1 玩法 last_delta 恒 0", err is None
         and eng2.handle_message(10001, 8001, "老张", "1") == "11"
         and eng2.last_delta == 0, err or "")

    # ---- 红包玩法协议：handle_redpacket ----
    rp_src = samples / "rule_redpacket.py"
    if rp_src.is_file():
        rp_dst = tmp / "rule_redpacket.py"
        rp_dst.write_text(rp_src.read_text(encoding="utf-8"), encoding="utf-8")
        err = eng2.activate(3, "rule_redpacket@1.0", rp_dst)
        step("红包玩法激活", err is None, err or "")
        step("has_redpacket_handler=True", eng2.has_redpacket_handler())
        r, d = eng2.handle_redpacket(10001, 1, "甲", 1.11)
        step("红包 1.11 → 回复+3分", r == "领取1.11元，获得3积分" and d == 3, f"{r!r},{d}")
        r, d = eng2.handle_redpacket(10001, 2, "乙", 0.15)
        step("红包 0.15 → 6分", d == 6, str(d))
        r, d = eng2.handle_redpacket(10001, 3, "丙", 0)
        step("金额0 → 不计", r is None and d == 0)
        err = eng2.activate(7, "v2demo@1.0", v2)
        step("切回无红包协议的玩法", err is None and not eng2.has_redpacket_handler())

    # ---- 局生命周期通知：handle_round_start / handle_round_end ----
    rh = tmp / "rule_round.py"
    rh.write_text('''"""局回调样本：开局进入下注期、收尾回待机（模拟大吃小玩法）。"""
PHASE = {"mode": "idle"}

def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    if t == "状态":
        return "betting" if PHASE["mode"] == "betting" else "idle"
    if t == "开始游戏" and PHASE["mode"] != "betting":
        PHASE["mode"] = "betting"
        return "游戏开始"
    return None

def handle_round_start(group_id):
    PHASE["mode"] = "betting"

def handle_round_end(group_id):
    PHASE["mode"] = "idle"
''', encoding="utf-8")
    err = eng2.activate(9, "rule_round@1.0", rh)
    step("局回调玩法激活", err is None, err or "")
    step("未开局为 idle", eng2.handle_message(10001, 9001, "甲", "状态") == "idle")
    eng2.notify_round_start(10001)
    step("notify_round_start 进入下注期", eng2.handle_message(10001, 9002, "乙", "状态") == "betting")
    eng2.notify_round_start(10001)  # 重复开局幂等（再次通知不应报错）
    step("重复开局通知无害", eng2.handle_message(10001, 9003, "丙", "状态") == "betting"
         and not eng2.last_error, eng2.last_error)
    eng2.notify_round_end(10001)
    step("notify_round_end 回到待机", eng2.handle_message(10001, 9004, "丁", "状态") == "idle")

    # 带局号的开局：handle_round_start(group_id, round_id) 收下局号 → 回复「本局 局号」名单
    rh2 = tmp / "rule_round2.py"
    rh2.write_text('''"""局回调样本：双参开局，保存局号供下注回复使用（对应玩法4 大吃小）。"""
BETS = {}
PHASE = {"mode": "idle"}
ROUND = {}

def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    if t == "状态":
        return "betting" if PHASE["mode"] == "betting" else "idle"
    if t == "局号":
        return ROUND.get(int(group_id), "")
    if t.startswith("下注") and PHASE["mode"] == "betting":
        qq = str(qq)
        if qq in BETS:
            return None
        BETS[qq] = {"nickname": nickname, "amount": int(t[2:])}
        rid = ROUND.get(int(group_id), "")
        head = f"本局 {rid}，" if rid else ""
        return head + "，".join(f"{b['nickname']}下注{b['amount']}" for b in BETS.values())
    return None

def handle_round_start(group_id, round_id):
    BETS.clear()
    PHASE["mode"] = "betting"
    ROUND[int(group_id)] = round_id

def handle_round_end(group_id):
    PHASE["mode"] = "idle"
    ROUND.pop(int(group_id), None)

def handle_round_abort(group_id):
    if PHASE["mode"] == "betting" and BETS:
        return "本局 已终止（积分已退还，不抽水）"
    return None

def bettor_qqs(group_id):
    return list(BETS) if PHASE["mode"] == "betting" else []
''', encoding="utf-8")
    err = eng2.activate(10, "rule_round2@1.0", rh2)
    step("带局号玩法激活", err is None, err or "")
    eng2.notify_round_start(10001, "hongbaojifen_00000099")
    step("双参开局收到局号", eng2.handle_message(10001, 9100, "甲", "局号") == "hongbaojifen_00000099")
    step("下注回复含局号+首名单", eng2.handle_message(10001, 9101, "甲", "下注100")
         == "本局 hongbaojifen_00000099，甲下注100")
    step("下注回复累计名单", eng2.handle_message(10001, 9102, "乙", "下注200")
         == "本局 hongbaojifen_00000099，甲下注100，乙下注200")
    step("bettor_qqs 给出下注者集合", eng2.rule_bettors(10001) == {"9101", "9102"},
         str(eng2.rule_bettors(10001)))
    step("下注期 query_round_abort 返回终止公告",
         eng2.query_round_abort(10001) == "本局 已终止（积分已退还，不抽水）",
         repr(eng2.query_round_abort(10001)))
    eng2.notify_round_end(10001)
    step("收尾清局号", eng2.handle_message(10001, 9103, "丙", "局号") is None
         and eng2.handle_message(10001, 9104, "丁", "状态") == "idle")
    step("收尾后 bettor_qqs 为空", eng2.rule_bettors(10001) == set())
    step("收尾后 query_round_abort 为空", eng2.query_round_abort(10001) is None)
    err = eng2.activate(11, "rule_add1@1.0", files["rule_add1"])
    step("无局回调的玩法通知为 no-op", err is None and eng2.handle_message(10001, 9005, "戊", "1") == "11"
         and (eng2.notify_round_start(10001) is None))
    step("无 bettor_qqs 玩法返回空集", eng2.rule_bettors(10001) == set())
    step("无 handle_round_abort 玩法 query 为空", eng2.query_round_abort(10001) is None)

    # ---- RoundSession：多群分局 / 结算上报 / 失败保留 ----
    fake = _FakeBackend()
    sess = RoundSession(fake, play_name="v2demo@1.0", play_id=7)
    sess.add_event("111111", 7001, "小红", "抢到1.23元", "未中雷", 0)
    sess.add_event("111111", 7002, "小蓝", "5", "答对 +1", 1)
    sess.add_event("222222", 7001, "小红", "中雷", "命中雷 -10", -10)
    step("RoundSession 两群分局", sess.pending_count() == 2
         and sess.round_info("111111")["event_count"] == 2
         and sess.round_info("111111")["total_delta"] == 1
         and sess.round_info("222222")["total_delta"] == -10)

    res = sess.settle("111111")
    step("结算111111 上报成功", res.get("round_id") and not res.get("duplicate")
         and len(fake.reports) == 1 and fake.reports[0]["group_id"] == "111111"
         and fake.reports[0]["play_name"] == "v2demo@1.0" and len(fake.reports[0]["events"]) == 2,
         f"reports={len(fake.reports)}")
    step("结算后111111 开新局, 222222 未动", sess.pending_count() == 1
         and sess.round_info("111111")["event_count"] == 0
         and sess.round_info("222222")["event_count"] == 1)
    step("空局结算返回 empty", sess.settle("111111").get("empty") is True)

    fake.fail = True
    sess.add_event("333333", 7003, "小绿", "1", "答对 +1", 1)
    try:
        sess.settle("333333")
        step("上报失败上抛 BackendError", False)
    except BackendError as e:
        step("上报失败上抛 BackendError", True, str(e))
    step("失败后事件保留", sess.round_info("333333")["event_count"] == 1
         and sess.pending_count() == 2)
    fake.fail = False
    sess.settle("333333")
    step("失败后可重试成功", sess.round_info("333333")["event_count"] == 0
         and sess.pending_count() == 1)

    fake.banned = True
    sess.add_event("444444", 7004, "小紫", "1", "答对 +1", 1)
    try:
        sess.settle("444444")
        step("封禁上报抛 ExecutorBanned", False)
    except ExecutorBanned:
        step("封禁上报抛 ExecutorBanned", True)
    step("封禁后事件保留待解封", sess.round_info("444444")["event_count"] == 1)

    print("\nplay_engine 自测" + ("通过 ✓" if ok else "存在失败 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_selftest())
