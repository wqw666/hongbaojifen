"""红包计分玩法：管理员发红包 → 成员领取按玩法规则计分 → 领完 @全体 结算。

积分规则由**总后台玩法文件**决定（handle_redpacket 协议，见 play_rules/rule_redpacket.py），
本模块只做玩法无关的编排：
- 只有群主/管理员发的红包参与玩法（get_admin_qqs 注入校验）
- 每个领取人调用 rule_handler(group_id, qq, nickname, amount) -> (回复|None, 积分)
  有回复则立即 @领取人（send_reply 注入）；无回复且无积分变动则跳过该领取
- 一局 = 一个红包（bill_no）。领完判定：插件汇总 recv_num>=total_num，
  或领取人数 >= 总份数，或领取人数 >= 群人数 → 自动结算：
    1) settle 上报总后台（round_id=bill_no 幂等，逐条入账）
    2) send_announce @全体 播报：用户X 领取A元，获得B积分 …
- 机器人自己领的不计；同人同包幂等（去重）；结算失败事件保留在 pending，
  下次事件到达或 retry_pending() 自动重试；上报成功后才播报（保证「说出口=已入账」）
- 开始本局模式（announced）：红包领完**或所有下注者均已领取**（玩法可选
  bettor_qqs(group_id) 提供下注者名单，get_bettors 注入）→ 立即 @全体「游戏结束」→
  10 秒后玩法批量结算（个人开奖回复）→ 再 10 秒后 @全体播报结算详情并上报结束本局
- 下注期手动「结束本局」= **提前终止**：玩法可选 handle_round_abort(group_id)（query_abort
  注入）返回终止公告文本 → 只 @全体 播报「本局已终止（积分已退还，不抽水）」并作废本局、
  不上报（玩法下注期从未扣分，退还即无操作）；返回 None → 走正常结算上报收尾

线程纪律：本类不做任何线程处理，由调用方（play_tab worker 单线程）串行调用。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from .backend_client import ExecutorBanned, OperatorDisabled

# 本局模式收尾节奏：领完/下注者都领 → 「游戏结束」 → SETTLE 秒后结算 → DETAIL 秒后播报详情
STAGE_SETTLE_SEC = 10
STAGE_DETAIL_SEC = 10


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _now_full() -> str:
    return datetime.now().strftime("%m-%d %H:%M:%S")


class RedPacketGame:
    """红包玩法会话。settle 失败时抛 ExecutorBanned/OperatorDisabled 上抛（调用方停摆）。"""

    def __init__(
        self,
        play_name: str = "红包玩法",
        play_id: int | str = 0,
        rule_handler: Callable[[str, str, str, float], tuple[str | None, int]] | None = None,
        rule_handler_batch: Callable[[str, list[dict], int], dict | None] | None = None,
        send_reply: Callable[[str, str, str], None] | None = None,
        send_announce: Callable[[str, str], None] | None = None,
        settle: Callable[[str, str, list[dict]], None] | None = None,
        get_admin_qqs: Callable[[str], set[str]] | None = None,
        get_bettors: Callable[[str], set[str]] | None = None,
        query_abort: Callable[[str], str | None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_round_end: Callable[[str], None] | None = None,
    ) -> None:
        self.play_name = play_name
        self.play_id = play_id
        self.rule_handler = rule_handler          # (group_id, qq, nickname, amount) -> (reply|None, delta)
        self.rule_handler_batch = rule_handler_batch  # (group_id, claims, rate) -> {events, announce}|None
        self.send_reply = send_reply              # (group_id, at_qq, text)
        self.send_announce = send_announce        # (group_id, text)
        self.settle = settle                      # (round_id, group_id, events) 失败抛异常
        self.get_admin_qqs = get_admin_qqs        # (group_id) -> set[str]
        self.get_bettors = get_bettors            # (group_id) -> 已下注者 QQ 集合（领完判定用）
        self.query_abort = query_abort            # (group_id) -> 终止公告文本|None（下注期手动「结束本局」问询）
        self.on_log = on_log                      # (msg) 日志回调
        self.on_round_end = on_round_end          # (group_id) 本局结束回调（自动收尾/作废时也通知）
        self.rounds: dict[str, dict] = {}         # bill_no -> 局（未点「开始本局」的传统模式）
        self.pending: dict[str, dict] = {}        # 结算失败待重试
        self.last: dict[str, dict] = {}           # bill_no/round_id -> {ok, error, time}
        self.announced: dict[str, dict] = {}      # 开始本局模式：group_id -> 当前局
        self.pending_announced: dict[str, dict] = {}  # 本局结算失败待重试
        self.closed_bills: dict[str, float] = {}  # 已收尾本局见过的红包单号 -> 结束时间（护栏）
        self.self_uin = ""
        self.group_member_count: dict[str, int] = {}

    def _log(self, msg: str) -> None:
        if self.on_log:
            try:
                self.on_log(msg)
            except Exception:
                pass

    # ---------- 事件入口 ----------

    def handle_claim(self, payload: dict) -> None:
        """处理插件推送的红包领取事件（幂等，可反复推送同一包）。"""
        gid = str(payload.get("group_id") or "")
        bill = str(payload.get("bill_no") or "")
        if not gid or not bill:
            return
        self.self_uin = str(payload.get("self_uin") or self.self_uin)
        sender = str(payload.get("sender_uin") or "")
        total_num = int(payload.get("total_num") or 0)
        recv_num = int(payload.get("recv_num") or 0)
        mc = int(payload.get("member_count") or 0)
        if mc:
            self.group_member_count[gid] = mc
        # 护栏：本局已收尾过的红包单号再推送（插件重复/延迟）→ 直接忽略，防幽灵局
        closed_at = self.closed_bills.get(bill)
        if closed_at and time.time() - closed_at < 300:
            self._log(f"[红包玩法] 群{gid} 单号{bill[:16]}… 本局已收尾，忽略重复推送")
            return

        # 只认管理员发的红包
        if not sender:
            self._log(f"[红包玩法] 群{gid} 单号{bill[:16]}… 跳过：未知发送者")
            return
        admins = self.get_admin_qqs(gid) if self.get_admin_qqs else set()
        if not admins:
            self._log(f"[红包玩法] 群{gid} 单号{bill[:16]}… 跳过：拉不到群主/管理员名单")
            return
        if sender not in admins:
            self._log(f"[红包玩法] 群{gid} 单号{bill[:16]}… 跳过：发送者 {sender} 非管理员")
            return

        key = bill
        ann = self.announced.get(gid)
        if ann is not None:
            # 开始本局模式：红包领取记入当前局（不收完红包 = 不立即结算，按阶段收尾）
            if ann.get("ended") or ann.get("claim_done"):
                return  # 已收尾/已进入收尾计时：后续红包事件不再受理
            ann.setdefault("bills", set()).add(bill)
            for c in payload.get("claims") or []:
                qq = str(c.get("qq") or "")
                if not qq or qq == self.self_uin:
                    continue
                if qq in ann["claims"]:
                    continue
                amount = float(c.get("amount") or 0)
                name = str(c.get("name") or "").strip() or f"用户{qq}"
                reply_text: str | None = None
                delta = 0
                if self.rule_handler:
                    try:
                        reply_text, delta = self.rule_handler(gid, qq, name, amount)
                    except Exception as e:  # noqa: BLE001
                        self._log(f"[红包玩法] 规则处理异常 {qq}: {e}")
                        reply_text, delta = None, 0
                if reply_text is None and delta == 0:
                    continue
                ts = self._claim_ts(c)
                ann["claims"][qq] = {"qq": qq, "name": name, "amount": amount,
                                     "delta": int(delta or 0), "reply": str(reply_text or "")}
                if len(ann["events"]) < 2000:
                    ann["events"].append({
                        "qq": qq, "nickname": name,
                        "msg": f"领取红包 {amount:.2f} 元",
                        "reply": str(reply_text or f"获得{int(delta or 0)}积分"),
                        "delta": int(delta or 0), "ts": ts,
                    })
                if reply_text and self.send_reply:
                    try:
                        self.send_reply(gid, qq, reply_text)
                    except Exception:
                        pass
                self._log(f"[红包玩法] 群{gid} {name}({qq}) 领取{amount:.2f}元 计分 +{int(delta or 0)}（本局 {ann['round_id']}）")
            # 领完判定：红包全领完 或 下注者均已领取 → 立即 @全体「游戏结束」→ 计时结算
            reason = self._claim_end_reason(gid, ann, total_num, recv_num)
            if reason:
                self._schedule_round_end(gid, ann, payload, reason)
            return

        r = self.rounds.get(key)
        if r is None:
            r = {
                "group_id": gid, "bill_no": bill, "sender_uin": sender,
                "sender_name": str(payload.get("sender_name") or ""),
                "total_num": total_num, "recv_num": 0,
                "claims": {}, "ended": False, "settled": False,
            }
            self.rounds[key] = r
            self._log(f"[红包玩法] 群{gid} 开新局 单号{bill[:16]}… 总份数 {total_num}")
        if total_num:
            r["total_num"] = max(r["total_num"], total_num)
        if recv_num:
            r["recv_num"] = max(r["recv_num"], recv_num)

        for c in payload.get("claims") or []:
            qq = str(c.get("qq") or "")
            if not qq or qq == self.self_uin:
                continue  # 机器人自己领的不计
            if qq in r["claims"]:
                continue  # 同人同包只计一次
            amount = float(c.get("amount") or 0)
            name = str(c.get("name") or "").strip() or f"用户{qq}"
            reply_text: str | None = None
            delta = 0
            if self.rule_handler:
                try:
                    reply_text, delta = self.rule_handler(gid, qq, name, amount)
                except Exception as e:  # noqa: BLE001 — 规则异常只丢本条
                    self._log(f"[红包玩法] 规则处理异常 {qq}: {e}")
                    reply_text, delta = None, 0
            if reply_text is None and delta == 0:
                continue  # 规则认为这条不计分不回复
            r["claims"][qq] = {"qq": qq, "name": name, "amount": amount,
                               "delta": int(delta or 0), "reply": str(reply_text or "")}
            if reply_text and self.send_reply:
                try:
                    self.send_reply(gid, qq, reply_text)
                except Exception:
                    pass  # 回复失败不影响计分
            self._log(f"[红包玩法] 群{gid} {name}({qq}) 领取{amount:.2f}元 计分 +{int(delta or 0)}")

        claimed = len(r["claims"])
        if r["recv_num"] < claimed:
            r["recv_num"] = claimed

        full = False
        if r["total_num"] and r["recv_num"] >= r["total_num"]:
            full = True
        if r["total_num"] and claimed >= r["total_num"]:
            full = True
        mc2 = self.group_member_count.get(gid, 0)
        if mc2 and claimed >= mc2:
            full = True

        if full and r["claims"] and not r["ended"]:
            self._finalize(r)

    # ---------- 本局模式：领完判定与分阶段收尾 ----------

    def _current_bettors(self, gid: str) -> set[str]:
        """当前局已下注者 QQ 集合（玩法可选 bettor_qqs(group_id) 提供；失败/未提供返回空）。"""
        if not self.get_bettors:
            return set()
        try:
            return {str(q) for q in (self.get_bettors(gid) or []) if str(q)}
        except Exception:  # noqa: BLE001
            return set()

    def _query_abort_text(self, gid: str) -> str | None:
        """问玩法：手动「结束本局」撞上下注期 → 本局能否按「提前终止」收。

        玩法 handle_round_abort 返回公告文本 → 终止（积分退还、不抽水、不上报）；
        返回 None / 玩法未提供 / 调用异常 → 正常收尾。"""
        if not self.query_abort:
            return None
        try:
            return self.query_abort(str(gid))
        except Exception:  # noqa: BLE001
            return None

    def _claim_end_reason(self, gid: str, ann: dict, total_num: int, recv_num: int) -> str:
        """领完/可收尾原因：'' = 未到时机；否则返回原因文案。

        三种情况都算「红包领完」：recv>=total（全领完）、领取人数 >= 群人数兜底，
        或玩法提供下注者名单且所有下注者都已领到（余量无所谓，如大吃小）。"""
        if total_num and recv_num and recv_num >= total_num:
            return f"红包已领完（{recv_num}/{total_num}）"
        mc2 = self.group_member_count.get(gid, 0)
        if mc2 and len(ann["claims"]) >= mc2:
            return f"领取人数已达群人数（{mc2}）"
        bettors = self._current_bettors(gid)
        if bettors and bettors <= set(ann["claims"]):
            return "所有下注玩家均已领取红包"
        return ""

    def _schedule_round_end(self, gid: str, ann: dict, payload: dict, reason: str) -> None:
        """红包领完/下注者都领 → 立即 @全体「游戏结束」，随后分阶段收尾。

        阶段：t0 游戏结束 → t0+STAGE_SETTLE_SEC 玩法批量结算（个人开奖回复）
             → t0+SETTLE+DETAIL 秒 @全体播报结算详情并上报结束本局（poll_auto_end 驱动）。"""
        ann["claim_done"] = True
        ann["last_payload"] = payload  # 结算阶段用这份领取明细跑玩法批量结算
        now = time.time()
        ann["settle_at"] = now + STAGE_SETTLE_SEC
        ann["detail_at"] = now + STAGE_SETTLE_SEC + STAGE_DETAIL_SEC
        text = (f"游戏结束（局号 {ann['round_id']}）：{reason}，"
                f"{STAGE_SETTLE_SEC} 秒后结算，结算后公布详情")
        if self.send_announce:
            try:
                self.send_announce(gid, text)
            except Exception:  # noqa: BLE001
                pass
        self._log(f"[红包玩法] 群{gid} 收尾计时开始：{reason}，"
                  f"{STAGE_SETTLE_SEC}s 后结算、{STAGE_SETTLE_SEC + STAGE_DETAIL_SEC}s 后播报详情")

    def _batch_settle_once(self, gid: str, ann: dict) -> None:
        """收尾结算阶段执行一次玩法批量结算（幂等：batch_done 置位防重）。"""
        if ann.get("batch_done"):
            return
        ann["batch_done"] = True
        self._apply_batch_settle(gid, ann, ann.get("last_payload") or {})

    # ---------- 结算与播报 ----------

    def _events_of(self, r: dict) -> list[dict]:
        return [
            {
                "qq": c["qq"],
                "nickname": c["name"],
                "text": f"领取红包 {c['amount']:.2f} 元",
                "reply": c.get("reply") or f"获得{c['delta']}积分",
                "delta": c["delta"],
            }
            for c in r["claims"].values()
        ]

    def _announce(self, r: dict) -> None:
        if not self.send_announce:
            return
        lines = [f"{c['name']} 领取{c['amount']:.2f}元，获得{c['delta']}积分"
                 for c in r["claims"].values()]
        sender = r.get("sender_name") or "管理员"
        text = f"本轮红包已领完（{sender} 发出的红包）：\n" + "\n".join(lines)
        try:
            self.send_announce(r["group_id"], text)
        except Exception:
            pass  # 播报失败不阻塞

    def _finalize(self, r: dict) -> None:
        """领完 → 上报总后台 → 成功才 @全体 播报。失败入 pending 待重试。"""
        r["ended"] = True
        if not self.settle:
            return
        try:
            self.settle(r["bill_no"], r["group_id"], self._events_of(r))
        except (ExecutorBanned, OperatorDisabled):
            raise  # 停摆上抛给调用方
        except Exception as e:  # noqa: BLE001 — 网络/参数错误等保留待重试
            self.pending[r["bill_no"]] = r
            self.last[r["bill_no"]] = {"ok": False, "error": str(e), "time": _now()}
            self._log(f"[红包玩法] 结算失败（保留待重试）: {e}")
            return
        r["settled"] = True
        self.last[r["bill_no"]] = {"ok": True, "error": "", "time": _now()}
        self._log(f"[红包玩法] 单号{r['bill_no'][:16]}… 已结算并上报")
        self._announce(r)

    # ---------- 开始本局 / 结束本局模式 ----------

    def start_round(self, group_id: str, round_id: str, play_name: str = "") -> None:
        """「开始本局」：该群进入本局模式，后续红包领取与聊天都记入本局回放。"""
        gid = str(group_id)
        self.announced[gid] = {
            "group_id": gid, "round_id": str(round_id), "play_name": play_name,
            "events": [], "claims": {}, "ended": False,
        }
        self._log(f"[红包玩法] 群{gid} 本局开始 局号 {round_id}")

    def record_chat(self, group_id: str, qq: str, nickname: str, text: str) -> None:
        """本局模式：记录群内聊天过程（不计分，回放用）。"""
        ann = self.announced.get(str(group_id))
        if ann is None or ann.get("ended"):
            return
        if not text or str(qq) == self.self_uin:
            return
        if len(ann["events"]) >= 2000:
            self._log("[红包玩法] 本局事件已达 2000 条上限，后续过程不再记录（请尽快结束本局）")
            return
        ann["events"].append({
            "qq": str(qq), "nickname": str(nickname or ""),
            "msg": str(text)[:512], "reply": "",
            "delta": 0, "ts": _now_full(),
        })

    def end_round(self, group_id: str) -> None:
        """「结束本局」：统计（领取人数/总积分/事件数）→ 上报（round_id=局号）→ @全体总结。
        上报成功才播报；失败保留 pending_announced 待重试。"""
        gid = str(group_id)
        ann = self.announced.get(gid)
        if ann is None:
            return
        if ann.get("ended"):
            self._log(f"[红包玩法] 群{gid} 本局已结束，请勿重复结束")
            return
        # 提前终止：还没到领完收尾（claim_done/batch_done 均未置位）就手动「结束本局」→
        # 先问玩法能否按终止收（大吃小下注期终止：积分退还、不抽水、本局不上报）。
        # 玩法返回公告文本 → 只 @全体 播报并作废本局；返回 None → 走下方正常结算收尾。
        if not ann.get("claim_done") and not ann.get("batch_done"):
            abort_text = self._query_abort_text(gid)
            if abort_text:
                rid = ann["round_id"]
                ann["ended"] = True
                self._close_round_bills(ann)
                if self.send_announce:
                    try:
                        self.send_announce(gid, str(abort_text))
                    except Exception:  # noqa: BLE001 — 播报失败不阻塞终止
                        pass
                del self.announced[gid]
                self._log(f"[红包玩法] 群{gid} 本局 {rid} 已终止（积分已退还，不抽水），不上报不结算")
                self._notify_round_end(gid)
                return
        # 自动收尾途中被手动「结束本局」：先补跑未执行的玩法批量结算（开奖回复/详情文本）
        if ann.get("claim_done") and not ann.get("batch_done"):
            self._batch_settle_once(gid, ann)
        ann["ended"] = True
        self._close_round_bills(ann)
        events = ann["events"]
        if not events:
            del self.announced[gid]
            self._log(f"[红包玩法] 群{gid} 本局无事件，已取消")
            self._notify_round_end(gid)
            return
        if not self.settle:
            return
        try:
            self.settle(ann["round_id"], gid, events)
        except (ExecutorBanned, OperatorDisabled):
            raise
        except Exception as e:  # noqa: BLE001
            self.pending_announced[ann["round_id"]] = ann
            self.last[ann["round_id"]] = {"ok": False, "error": str(e), "time": _now()}
            self._log(f"[红包玩法] 本局结算失败（保留待重试）: {e}")
            return
        self.last[ann["round_id"]] = {"ok": True, "error": "", "time": _now()}
        self._announce_round_summary(ann)
        del self.announced[gid]
        self._log(f"[红包玩法] 本局 {ann['round_id']} 已结算上报并播报总结")
        self._notify_round_end(gid)

    def _announce_round_summary(self, ann: dict) -> None:
        """@全体 总结语：领取人逐行 + 统计。"""
        claims = list(ann["claims"].values())
        if not self.send_announce:
            return
        # 玩法自定义播报文本（大吃小等）优先
        if ann.get("announce_text"):
            try:
                self.send_announce(ann["group_id"], str(ann["announce_text"]))
            except Exception:
                pass
            return
        if not claims:
            return
        lines = [f"{c['name']} 领取{c['amount']:.2f}元，获得{c['delta']}积分" for c in claims]
        total = sum(c["delta"] for c in claims)
        text = (f"本局结束统计（局号 {ann['round_id']}）：共 {len(claims)} 人领取，"
                f"总积分 {total}\n" + "\n".join(lines))
        try:
            self.send_announce(ann["group_id"], text)
        except Exception:
            pass

    def _apply_batch_settle(self, gid: str, ann: dict, payload: dict) -> None:
        """调用玩法 settle_redpacket 批量结算：应用多玩家事件 + 保存播报文本。"""
        if not self.rule_handler_batch:
            return
        rate = int(payload.get("rate_permille") or 20)
        try:
            result = self.rule_handler_batch(gid, list(payload.get("claims") or []), rate)
        except Exception as e:  # noqa: BLE001
            self._log(f"[红包玩法] 批量结算异常: {e}")
            return
        if not isinstance(result, dict):
            return
        if result.get("announce"):
            ann["announce_text"] = str(result["announce"])
        for ev in result.get("events") or []:
            qq = str(ev.get("qq") or "")
            if not qq or qq == self.self_uin or qq == "announce":
                continue
            delta = int(ev.get("delta") or 0)
            reply_text = str(ev.get("reply") or "")
            nickname = str(ev.get("nickname") or "")
            if len(ann["events"]) < 2000:
                ann["events"].append({
                    "qq": qq, "nickname": nickname,
                    "msg": "玩法结算",
                    "reply": reply_text,
                    "delta": delta, "ts": _now_full(),
                })
            if reply_text and self.send_reply:
                try:
                    self.send_reply(gid, qq, reply_text)
                except Exception:
                    pass
            self._log(f"[红包玩法] 群{gid} 结算 {nickname or qq}({qq}) delta {delta:+d}")

    def _claim_ts(self, c: dict) -> str:
        """领取时间 → 'MM-dd HH:mm:ss'（回放用；缺省取当前时间）。"""
        try:
            t = float(c.get("time") or 0)
            if t > 0:
                return datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            pass
        return _now_full()

    def poll_auto_end(self) -> None:
        """worker 定时调用：驱动分阶段收尾（游戏结束 → +SETTLE秒 批量结算 → +DETAIL秒 详情并收局）。

        阶段 1（settle_at）：玩法批量结算——大吃小等开奖，个人「赢/亏」回复随结算发出；
        阶段 2（detail_at）：上报本局并 @全体 播报结算详情（玩法自定义 announce 文本优先）。"""
        for gid, ann in list(self.announced.items()):
            if ann.get("ended") or not ann.get("claim_done"):
                continue
            now = time.time()
            if not ann.get("batch_done") and now >= (ann.get("settle_at") or now + 1):
                self._batch_settle_once(gid, ann)
                self._log(f"[红包玩法] 群{gid} 红包结算完成（{ann['round_id']}），"
                          f"{max(0, int((ann.get('detail_at') or 0) - now))}s 后播报详情")
            if now >= (ann.get("detail_at") or 0):
                try:
                    self.end_round(gid)
                except (ExecutorBanned, OperatorDisabled):
                    raise
                except Exception as e:  # noqa: BLE001
                    self._log(f"[红包玩法] 自动结束本局异常: {e}")

    def cancel_rounds(self) -> int:
        """作废所有进行中的本局（终止连续模式时调用：数据不上报、不播报）。"""
        items = list(self.announced.items())
        n = len(items)
        if n:
            self.announced.clear()
            for gid, ann in items:
                self._close_round_bills(ann)
                self._notify_round_end(gid)
            self._log(f"[红包玩法] 已作废 {n} 个进行中的本局（数据不上报）")
        return n

    def _notify_round_end(self, gid: str) -> None:
        """局收尾通知玩法（玩法可选的 handle_round_end；回调异常不阻断）。"""
        if not self.on_round_end:
            return
        try:
            self.on_round_end(str(gid))
        except Exception:  # noqa: BLE001
            pass

    def _close_round_bills(self, ann: dict) -> None:
        """本局收尾：记录本局见过的红包单号（后续插件重复推送将被忽略，防幽灵局）。"""
        now = time.time()
        for bill in ann.get("bills") or []:
            self.closed_bills[str(bill)] = now

    def has_active_round(self, group_id: str) -> bool:
        ann = self.announced.get(str(group_id))
        return ann is not None and not ann.get("ended")

    def retry_pending(self) -> None:
        """重试结算失败的红包局（每次事件到达/启用玩法时调用）。"""
        for bill, r in list(self.pending.items()):
            try:
                self.settle(r["bill_no"], r["group_id"], self._events_of(r))
            except (ExecutorBanned, OperatorDisabled):
                raise
            except Exception as e:  # noqa: BLE001
                self.last[bill] = {"ok": False, "error": str(e), "time": _now()}
                continue
            del self.pending[bill]
            r["settled"] = True
            self.last[bill] = {"ok": True, "error": "", "time": _now()}
            self._log(f"[红包玩法] 补结算成功 单号{bill[:16]}…")
            self._announce(r)
        for rid, ann in list(self.pending_announced.items()):
            try:
                self.settle(ann["round_id"], ann["group_id"], ann["events"])
            except (ExecutorBanned, OperatorDisabled):
                raise
            except Exception as e:  # noqa: BLE001
                self.last[rid] = {"ok": False, "error": str(e), "time": _now()}
                continue
            del self.pending_announced[rid]
            self.last[rid] = {"ok": True, "error": "", "time": _now()}
            self._announce_round_summary(ann)
            self._log(f"[红包玩法] 本局补结算成功 {rid}")

    def pending_count(self) -> int:
        return len(self.pending) + len(self.pending_announced)


def _selftest() -> int:
    """单元自测：规则注入、去重、管理员过滤、领完结算与播报。"""
    ok = 0
    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    def rule(gid, qq, nickname, amount):
        """模拟 rule_redpacket.py：积分=金额各位数之和。"""
        a = float(amount or 0)
        if a <= 0:
            return None, 0
        s = f"{a:.2f}".replace(".", "")
        points = sum(int(ch) for ch in s if ch.isdigit())
        return f"领取{a:.2f}元，获得{points}积分", points

    replies: list[tuple] = []
    announces: list[tuple] = []
    settled: list[tuple] = []
    logs: list[str] = []

    def send_reply(g, q, t):
        replies.append((g, q, t))
    def send_announce(g, t):
        announces.append((g, t))
    def settle(rid, g, events):
        settled.append((rid, g, events))
    admins = {"414744169": {"909736102"}}  # 群主

    game = RedPacketGame(play_name="红包玩法", play_id=2,
                         rule_handler=rule, send_reply=send_reply,
                         send_announce=send_announce, settle=settle,
                         get_admin_qqs=lambda g: admins.get(g, set()),
                         on_log=logs.append)
    # 普通成员发的红包 → 忽略
    game.handle_claim({"group_id": "414744169", "bill_no": "B1", "sender_uin": "999",
                       "total_num": 2, "recv_num": 2,
                       "claims": [{"qq": "111", "name": "甲", "amount": 1.11}]})
    check("非管理员红包忽略", not game.rounds and not replies)
    check("跳过有日志", any("非管理员" in l for l in logs))

    # 管理员红包：先 甲 领（机器人自己也领了一份，不计分）→ 未领完不结算
    game.handle_claim({"group_id": "414744169", "bill_no": "B2", "sender_uin": "909736102",
                       "sender_name": "群主", "total_num": 2, "recv_num": 1,
                       "self_uin": "1125163007",
                       "claims": [{"qq": "111", "name": "甲", "amount": 1.11},
                                  {"qq": "1125163007", "name": "机器人", "amount": 0.30}]})
    check("回复1条且@甲", len(replies) == 1 and replies[0][1] == "111", str(replies))
    check("机器人自己不计", "1125163007" not in game.rounds["B2"]["claims"])
    check("未领完未结算", not settled and not announces)

    # 乙 领取 → 领完（recv_num=2）→ 自动结算 + @全体
    game.handle_claim({"group_id": "414744169", "bill_no": "B2", "sender_uin": "909736102",
                       "total_num": 2, "recv_num": 2,
                       "claims": [{"qq": "111", "name": "甲", "amount": 1.11},
                                  {"qq": "222", "name": "乙", "amount": 0.15}]})
    check("领完自动结算", len(settled) == 1 and settled[0][0] == "B2")
    check("事件 delta 正确",
          {e["qq"]: e["delta"] for e in settled[0][2]} == {"111": 3, "222": 6},
          str(settled))
    check("@全体播报", len(announces) == 1 and "甲" in announces[0][1] and "乙" in announces[0][1],
          str(announces))
    # 幂等：同一包重复推送不重复计分
    game.handle_claim({"group_id": "414744169", "bill_no": "B2", "sender_uin": "909736102",
                       "total_num": 2, "recv_num": 2,
                       "claims": [{"qq": "111", "name": "甲", "amount": 1.11},
                                  {"qq": "222", "name": "乙", "amount": 0.15}]})
    check("重复推送幂等", len(replies) == 2 and len(settled) == 1 and len(announces) == 1)

    # 本局模式：局收尾（自动/手动结束、作废）都要通知玩法 handle_round_end
    ended: list[str] = []
    gm = RedPacketGame(play_name="玩法", play_id=9,
                       rule_handler=rule, settle=settle,
                       get_admin_qqs=lambda g: admins.get(g, set()),
                       on_log=logs.append, on_round_end=ended.append)
    gm.start_round("414744169", "hongbaojifen_00000100", "玩法")
    check("无事件结束本局也通知", gm.has_active_round("414744169"))
    gm.end_round("414744169")
    check("无事件局取消并通知", not gm.has_active_round("414744169") and ended == ["414744169"],
          str(ended))
    gm.start_round("414744169", "hongbaojifen_00000101", "玩法")
    gm.record_chat("414744169", "111", "甲", "下注100")
    gm.end_round("414744169")
    check("正常结束本局通知", ended[-1] == "414744169", str(ended))
    gm.start_round("414744169", "hongbaojifen_00000102", "玩法")
    gm.start_round("555555", "hongbaojifen_00000103", "玩法")
    n = gm.cancel_rounds()
    check("作废本局逐群通知", n == 2 and ended.count("414744169") == 3
         and ended.count("555555") == 1, str(ended))

    # ---- 本局模式分阶段收尾：下注者都领 → 「游戏结束」→ +10s 批量结算 → +10s 详情 ----
    msgs_r2: list[tuple] = []
    msgs_a2: list[tuple] = []
    settled2: list[tuple] = []
    ended2: list[str] = []

    def batch_rule2(g, claims, rate):
        return {"events": [{"qq": str(c["qq"]), "nickname": str(c.get("name") or ""),
                            "reply": f"开奖结算 {str(c.get('amount') or '')}", "delta": 5}
                           for c in claims],
                "announce": f"结算详情：共{len(claims)}人参与"}

    gm2 = RedPacketGame(play_name="玩法", play_id=10, rule_handler=rule,
                        rule_handler_batch=batch_rule2,
                        send_reply=lambda g, q, t: msgs_r2.append((g, q, t)),
                        send_announce=lambda g, t: msgs_a2.append((g, t)),
                        settle=lambda rid, g, ev: settled2.append((rid, g, ev)),
                        get_admin_qqs=lambda g: admins.get(g, set()),
                        get_bettors=lambda g: {"111", "222"},
                        on_round_end=ended2.append)
    gm2.start_round("414744169", "hongbaojifen_00000200", "玩法")
    gm2.group_member_count["414744169"] = 100  # 群人数兜底不干扰
    # 红包 99 份只发来甲一份 → 未到收尾
    gm2.handle_claim({"group_id": "414744169", "bill_no": "B9", "sender_uin": "909736102",
                      "total_num": 99, "recv_num": 1,
                      "claims": [{"qq": "111", "name": "甲", "amount": 1.11}]})
    ann = gm2.announced["414744169"]
    check("下注者未领齐不收尾", not ann.get("claim_done") and not msgs_a2, str(msgs_a2))
    # 甲+乙都领 → 下注者齐（红包未领完也收尾）→ 立即 @全体 游戏结束，不结算
    gm2.handle_claim({"group_id": "414744169", "bill_no": "B9", "sender_uin": "909736102",
                      "total_num": 99, "recv_num": 2,
                      "claims": [{"qq": "111", "name": "甲", "amount": 1.11},
                                 {"qq": "222", "name": "乙", "amount": 0.15}]})
    check("下注者都领进入收尾计时", ann.get("claim_done") and not ann.get("batch_done"))
    check("@全体先播游戏结束", len(msgs_a2) == 1 and "游戏结束" in msgs_a2[0][1], str(msgs_a2))
    # 到 10s 结算时刻：玩法批量结算跑一次（个人开奖回复发出），详情未播
    ann["settle_at"] = time.time() - 1
    gm2.poll_auto_end()
    check("10s 后批量结算一次", ann.get("batch_done")
         and any("开奖结算" in t for _, _, t in msgs_r2), str(msgs_r2))
    check("详情阶段未到不播报", len(msgs_a2) == 1)
    # 再 10s：上报 + @全体 结算详情 + 通知玩法收局
    ann["detail_at"] = time.time() - 1
    gm2.poll_auto_end()
    check("详情播报+本局上报", len(msgs_a2) == 2 and "结算详情" in msgs_a2[-1][1]
         and settled2 and settled2[0][0] == "hongbaojifen_00000200", str(msgs_a2))
    check("收局并通知玩法", ended2 == ["414744169"] and not gm2.has_active_round("414744169"))
    # 收尾后重复推送的领取事件被忽略（不重复回复/不重复播报）
    n_replies = len(msgs_r2)
    gm2.handle_claim({"group_id": "414744169", "bill_no": "B9", "sender_uin": "909736102",
                      "total_num": 99, "recv_num": 3,
                      "claims": [{"qq": "111", "name": "甲", "amount": 1.11},
                                 {"qq": "222", "name": "乙", "amount": 0.15},
                                 {"qq": "333", "name": "路人", "amount": 0.05}]})
    check("收尾后事件忽略", len(msgs_r2) == n_replies and len(msgs_a2) == 2)

    # 全领完路径（无下注者）：recv>=total → 同样分阶段收尾
    gm2.start_round("414744169", "hongbaojifen_00000201", "玩法")
    gm2.handle_claim({"group_id": "414744169", "bill_no": "B10", "sender_uin": "909736102",
                      "total_num": 1, "recv_num": 1,
                      "claims": [{"qq": "111", "name": "甲", "amount": 0.01}]})
    ann = gm2.announced["414744169"]
    check("红包全领完也进收尾计时", ann.get("claim_done"), str(ann.get("claim_done")))
    gm2.cancel_rounds()

    # ---- 提前终止：下注期手动「结束本局」→ 玩法 handle_round_abort 返回公告 → 只播报作废、不上报 ----
    msgs_a3: list[tuple] = []
    settled3: list[tuple] = []
    ended3: list[str] = []
    gm3 = RedPacketGame(play_name="玩法", play_id=11, rule_handler=rule,
                        send_announce=lambda g, t: msgs_a3.append((g, t)),
                        settle=lambda rid, g, ev: settled3.append((rid, g, ev)),
                        query_abort=lambda g: "本局 hongbaojifen_00000300 已终止（积分已退还，不抽水），等待管理员重新开局",
                        on_round_end=ended3.append)
    gm3.start_round("414744169", "hongbaojifen_00000300", "玩法")
    gm3.record_chat("414744169", "111", "甲", "下注100")  # 下注期已有人下注（过程事件）
    gm3.end_round("414744169")
    check("终止：@全体 收到终止公告（含积分已退还）",
          len(msgs_a3) == 1 and "已终止" in msgs_a3[0][1] and "积分已退还" in msgs_a3[0][1],
          str(msgs_a3))
    check("终止：不上报不结算", not settled3, str(settled3))
    check("终止：本局作废并通知玩法收尾", not gm3.has_active_round("414744169")
         and ended3 == ["414744169"], str(ended3))
    # 玩法说不可终止（返回 None）→ 走正常结算上报收尾
    msgs_a4: list[tuple] = []
    gm4 = RedPacketGame(play_name="玩法", play_id=12, rule_handler=rule,
                        send_announce=lambda g, t: msgs_a4.append((g, t)),
                        settle=lambda rid, g, ev: settled3.append((rid, g, ev)),
                        query_abort=lambda g: None,
                        on_round_end=ended3.append)
    gm4.start_round("414744169", "hongbaojifen_00000301", "玩法")
    gm4.record_chat("414744169", "111", "甲", "下注100")
    gm4.end_round("414744169")
    check("玩法返回 None → 正常上报收尾", len(settled3) == 1
         and settled3[0][0] == "hongbaojifen_00000301" and ended3[-1] == "414744169",
         str(settled3))

    print(f"selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
