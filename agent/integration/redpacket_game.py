"""红包计分玩法：管理员发红包 → 成员领取按玩法规则计分 → 领完 @全体 结算。

积分规则由**总后台玩法文件**决定（handle_redpacket 协议，见 play_rules/rule_fuhe.py），
本模块只做玩法无关的编排：
- 只有群主/管理员发的红包参与玩法（get_admin_qqs 注入校验）
- 每个领取人调用 rule_handler(group_id, qq, nickname, amount) -> (回复|None, 积分)
  有回复则立即 @领取人（send_reply 注入）；显式空串回执 = 记账但不发消息（静默领取）；
  无回复（None）且无积分变动则跳过该领取（不记录）
- 一局 = 一个红包（bill_no）。领完判定：插件汇总 recv_num>=total_num，
  或领取人数 >= 总份数，或领取人数 >= 群人数 → 自动结算：
    1) settle 上报总后台（round_id=bill_no 幂等，逐条入账）
    2) send_announce @全体 播报：用户X 领取A元，获得B积分 …
- 机器人自己领的不计；同人同包幂等（去重）；结算失败事件保留在 pending，
  下次事件到达或 retry_pending() 自动重试；上报成功后才播报（保证「说出口=已入账」）
- 开始本局模式（announced）= **手动结算驱动**：红包事件记入当前局，只认第 1 个管理员
  红包（D6）；封盘后红包份数 < 需开奖人数 → 立即作废（A3，积分未扣）；「结算」由 GUI
  按钮调 settle_round_now（玩法批量结算 → 上报成功才 @全体 播报并清局）；「作废本局」
  调 void_round（@全体 公告 → 关局，不上报不结算）
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
        claim_need: Callable[[str], int] | None = None,
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
        self._claim_need = claim_need             # (group_id) -> 本局需开奖人数（A3 作废/可结算判定）
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
            # 开始本局模式（复合玩法）：红包事件记入当前局；只认第 1 个管理员红包（D6）
            if ann.get("ended"):
                return  # 已收尾：后续红包事件不再受理
            fb = ann.get("first_bill")
            if fb is None:
                ann["first_bill"] = {"bill": bill, "total_num": total_num, "recv_num": recv_num}
            elif str(fb.get("bill")) != bill:
                self._log(f"[红包玩法] 群{gid} 只认第 1 个红包（单号 {fb['bill'][:12]}…），"
                          f"第 2 个红包单号 {bill[:12]}… 忽略不计入")
                ann.setdefault("bills", set()).add(bill)  # 单号进护栏：局收尾后补推也不落 legacy
                return  # 不接受第 2 个管理员红包
            else:
                fb["total_num"] = max(int(fb.get("total_num") or 0), total_num)
                fb["recv_num"] = max(int(fb.get("recv_num") or 0), recv_num)
            ann.setdefault("bills", set()).add(bill)
            # 封盘后不再覆盖费率：封盘公告 fee 与结算 fee 同口径（正常流=先封盘后红包，
            # 此时 sealed 已置 True；首红包先于封盘的异常流仍会写入，封盘时 GUI 会再覆写一次）
            if int(payload.get("rate_permille") or 0) > 0 and not ann.get("sealed"):
                ann["rate_permille"] = int(payload["rate_permille"])
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
                        "reply": str(reply_text or ""),
                        "delta": int(delta or 0), "ts": ts,
                    })
                if reply_text and self.send_reply:
                    try:
                        self.send_reply(gid, qq, reply_text)
                    except Exception:
                        pass
                self._log(f"[红包玩法] 群{gid} {name}({qq}) 领取{amount:.2f}元 计分 +{int(delta or 0)}"
                          f"（本局 {ann['round_id']}）")
            # A3 作废判定 + 可结算状态（不再自动收尾；结算由 GUI「结算」按钮手动驱动）
            self._evaluate_round(gid, ann, payload)
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

    # ---------- 本局模式：下注者名单与提前终止问询 ----------

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
            "sealed": False, "first_bill": None, "ready_to_settle": False,
            "rate_permille": 20, "batch_applied": False, "settle_results": [],
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

    def _need_claimers(self, gid: str) -> int:
        """本局需开奖人数（玩法 claim_need；缺省=已下注者数）。"""
        if self._claim_need:
            try:
                n = int(self._claim_need(str(gid)) or 0)
                if n > 0:
                    return n
            except Exception:  # noqa: BLE001
                pass
        return len(self._current_bettors(gid))

    def _evaluate_round(self, gid: str, ann: dict, payload: dict | None = None) -> None:
        """红包事件后 / 封盘时的本局状态评估（A3）：
        1) 封盘后已见首红包且份数 < 需开奖人数 → 立即作废（不必等领完）并播报；
        2) 封盘后领完（recv>=total）或参与领取人数>=需开奖人数 → ready_to_settle=True。"""
        fb = ann.get("first_bill")
        total = int((fb or {}).get("total_num") or 0)
        if fb and ann.get("sealed") and total and total < self._need_claimers(gid):
            need = self._need_claimers(gid)
            self._log(f"[红包玩法] 群{gid} 红包 {total} 份 < 需开奖 {need} 人 → 本局作废（积分未扣）")
            text = (f"⚠ 管理员红包份数（{total}）少于本局需开奖人数（{need}），"
                    f"本局作废（积分未扣），请重新开局。")
            self.void_round(gid, text)
            return
        if not ann.get("sealed"):
            return
        need = self._need_claimers(gid)
        recv = int((fb or {}).get("recv_num") or 0)
        ready = bool(fb and need > 0 and
                     ((total and recv >= total) or len(ann["claims"]) >= need))
        if ready and not ann.get("ready_to_settle"):
            ann["ready_to_settle"] = True
            self._log(f"[红包玩法] 群{gid} 红包已领完（{recv}/{total}）→ 可点「结算」")

    def seal_round(self, group_id: str) -> None:
        """「停止下注」成功后调用：置 sealed 并做 A3 首红包不足立即作废判定。幂等。"""
        gid = str(group_id)
        ann = self.announced.get(gid)
        if ann is None or ann.get("sealed"):
            return
        ann["sealed"] = True
        self._evaluate_round(gid, ann)
        if gid in self.announced:
            self._log(f"[红包玩法] 群{gid} 已封盘（等红包领完 → 手动「结算」）")

    def set_claim_amount(self, group_id: str, qq: str, amount: float,
                         nickname: str = "") -> str | None:
        """表格改/补开奖金额（改红包金额，不改下注）：0.01~0.99 两位小数校验。

        已抢者改值（留日志）；未抢的下注者/庄家补值视同抢到该金额（edited=True 标记，
        settle_round_now 用最新值结算）。返回错误文本或 None。"""
        gid = str(group_id)
        ann = self.announced.get(gid)
        if ann is None or ann.get("ended"):
            return "当前群没有进行中的本局"
        try:
            amt = float(amount or 0)
        except (TypeError, ValueError):
            return "金额必须是数字"
        if not (0.01 <= amt <= 0.99):
            return "金额须在 0.01~0.99 之间（两位小数）"
        qq_s = str(qq)
        old = None
        c = ann["claims"].get(qq_s)
        if c is not None:
            old = float(c.get("amount") or 0)
            c["amount"] = round(amt, 2)
            c["edited"] = True
            if nickname:
                c["name"] = nickname
            name = str(c.get("name") or f"用户{qq_s}")
        else:
            name = nickname or f"用户{qq_s}"
            ann["claims"][qq_s] = {"qq": qq_s, "name": name,
                                   "amount": round(amt, 2), "delta": 0,
                                   "reply": "", "edited": True}
        # 回放记录管理员改/补开奖：delta=0 只进时间线，不产生积分流水也不触发 warning（R7-5）
        if len(ann["events"]) < 2000:
            msg = (f"管理员改开奖金额：{old:.2f}→{amt:.2f} 元" if old is not None
                   else f"管理员补值开奖：{amt:.2f} 元")
            ann["events"].append({"qq": qq_s, "nickname": name, "msg": msg,
                                  "reply": "", "delta": 0, "ts": _now_full()})
        self._log(f"[红包玩法] 群{gid} 管理员改开奖：{qq_s} "
                  + (f"{old:.2f}→{amt:.2f}" if old is not None else f"补值 {amt:.2f}"))
        return None

    def settle_round_now(self, group_id: str) -> dict:
        """「结算」：合并表格改值/补值的 claims → 玩法批量结算（batch_applied 防重）→ 上报。

        上报成功才 @全体 播报总结并清局（说出口=已入账）；失败 ann 保留（ended 回滚）、
        进 pending_announced 待「重试未上报」。返回 {ok:True, results} 或 {ok:False, error}。
        results = 批量结算产出的 [{qq,nickname,delta}]（含 0/平局行），供 GUI 表格展示。"""
        gid = str(group_id)
        ann = self.announced.get(gid)
        if ann is None:
            return {"ok": False, "error": "当前群没有进行中的本局"}
        if ann.get("ended"):
            return {"ok": False, "error": "本局已结束/收尾中，请勿重复结算"}
        ann["ended"] = True  # 防重入；任一步失败回滚
        if not ann.get("batch_applied"):
            claims = [{"qq": c["qq"], "nickname": c.get("name") or "",
                       "amount": c.get("amount") or 0}
                      for c in ann["claims"].values()]
            n0 = len(ann["events"])
            self._apply_batch_settle(gid, ann, claims)
            results = [{"qq": str(ev.get("qq") or ""), "nickname": str(ev.get("nickname") or ""),
                        "delta": int(ev.get("delta") or 0)}
                       for ev in ann["events"][n0:] if str(ev.get("qq") or "")]
            ann["settle_results"] = results
            if not results:
                ann["ended"] = False
                return {"ok": False, "error": "玩法结算无产出（先发红包开奖，或双击表格补开奖金额）"}
            ann["batch_applied"] = True
        if not self.settle:
            ann["ended"] = False
            return {"ok": False, "error": "未配置总后台上报"}
        try:
            self.settle(ann["round_id"], gid, ann["events"])
        except (ExecutorBanned, OperatorDisabled):
            ann["ended"] = False
            raise
        except Exception as e:  # noqa: BLE001 — 网络/参数错误保留待重试
            ann["ended"] = False
            self.pending_announced[ann["round_id"]] = ann
            self.last[ann["round_id"]] = {"ok": False, "error": str(e), "time": _now()}
            self._log(f"[红包玩法] 本局结算上报失败（保留待重试）: {e}")
            return {"ok": False, "error": str(e)}
        self.pending_announced.pop(ann["round_id"], None)
        self.last[ann["round_id"]] = {"ok": True, "error": "", "time": _now()}
        self._close_round_bills(ann)
        self._announce_round_summary(ann)
        del self.announced[gid]
        self._log(f"[红包玩法] 本局 {ann['round_id']} 已结算上报并播报总结")
        self._notify_round_end(gid)
        return {"ok": True, "results": ann.get("settle_results") or []}

    def void_round(self, group_id: str, announce_text: str | None = None) -> None:
        """作废本局（红包不足/庄家余额不足/手动）：@全体 公告 → 关局，不上报不结算。

        同时清掉同局待重试记录（若有）。幂等：本局不在 announced 则 no-op。"""
        gid = str(group_id)
        ann = self.announced.pop(gid, None)
        if ann is None:
            return
        if announce_text and self.send_announce:
            try:
                self.send_announce(gid, str(announce_text))
            except Exception:  # noqa: BLE001
                pass
        self.pending_announced.pop(ann.get("round_id") or "", None)
        self._close_round_bills(ann)
        self._log(f"[红包玩法] 群{gid} 本局 {ann.get('round_id')} 已作废（不上报不结算）")
        self._notify_round_end(gid)

    def _announce_round_summary(self, ann: dict) -> None:
        """@全体 总结语：领取人逐行 + 统计。"""
        claims = list(ann["claims"].values())
        if not self.send_announce:
            return
        # 玩法自定义播报文本（大吃小等）优先；规则带 img 键时一并交给播报（图片化表格）
        if ann.get("announce_text"):
            try:
                self.send_announce(ann["group_id"], str(ann["announce_text"]),
                                   ann.get("announce_img") or None)
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

    def _apply_batch_settle(self, gid: str, ann: dict, claims: list[dict]) -> None:
        """调用玩法 settle_redpacket 批量结算：应用多玩家事件 + 发送个人开奖回复 + 存播报文本。

        claims: 本局最终领取明细（已含表格改值/补值；未领取者由玩法按 0 处理）。
        rate 取 ann['rate_permille']（由红包事件/GUI 封盘写入，见 Task 4）。"""
        if not self.rule_handler_batch:
            return
        rate = int(ann.get("rate_permille") or 20)
        try:
            result = self.rule_handler_batch(gid, list(claims or []), rate)
        except Exception as e:  # noqa: BLE001
            self._log(f"[红包玩法] 批量结算异常: {e}")
            return
        if not isinstance(result, dict):
            return
        if result.get("announce"):
            ann["announce_text"] = str(result["announce"])
        if isinstance(result.get("img"), dict):
            ann["announce_img"] = result["img"]
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
        """worker 每 0.5s 调用点：分阶段自动收尾已废弃（改为 GUI「结算」按钮手动驱动）。
        保留空壳兼容调用方；无自动行为。"""
        return

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
            self.announced.pop(str(ann.get("group_id") or ""), None)  # 防同局残留重复结算
            self._notify_round_end(str(ann.get("group_id") or ""))
            self._close_round_bills(ann)  # 首红包单号进护栏：插件补推不再落 legacy 二次入账
            self._log(f"[红包玩法] 本局补结算成功 {rid}")

    def pending_count(self) -> int:
        return len(self.pending) + len(self.pending_announced)


def _selftest() -> int:
    """单元自测：手动结算驱动 + 首红包不足作废 + 只认首红包 + 表格改值/补值 + 幂等/重试。"""
    ok = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    def rule(gid, qq, nickname, amount):
        """模拟玩法 handle_redpacket 回执：金额合法给提示，delta 0。"""
        try:
            amt = float(amount or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if not (0 < amt <= 0.99):
            return "金额异常", 0
        return f"已记录 {amt:.2f} 元", 0

    def batch_rule(gid, claims, rate):
        """模拟玩法 settle_redpacket：delta = 点数（改值 0.77 → 4 可断言参与）。"""
        events = []
        for c in claims or []:
            try:
                amt = float(c.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            pts = 0
            if 0 < amt <= 0.99:
                s = f"{amt:.2f}"
                pts = (int(s[2]) + int(s[3])) % 10
            events.append({"qq": str(c["qq"]), "nickname": str(c.get("nickname") or c["qq"]),
                           "reply": f"结算点{pts}", "delta": pts})
        return {"events": events, "announce": "结算详情（模拟）"}

    replies, announces, settled, logs, ended = [], [], [], [], []
    failed_once = {"cnt": 0}

    def settle(rid, g, events):
        if failed_once["cnt"] == 0:  # 第一次上报模拟失败 → 测 pending 重试
            failed_once["cnt"] += 1
            raise RuntimeError("模拟网络失败")
        settled.append((rid, g, events))

    game = RedPacketGame(play_name="复合玩法", play_id=77,
                         rule_handler=rule, rule_handler_batch=batch_rule,
                         send_reply=lambda g, q, t: replies.append((g, q, t)),
                         send_announce=lambda g, t, img=None: announces.append((g, t)),
                         settle=settle,
                         get_admin_qqs=lambda g: admins.get(g, set()),
                         get_bettors=lambda g: {"111", "222"},
                         claim_need=lambda g: 2 if g == "414744169" else 0,
                         on_log=logs.append, on_round_end=ended.append)
    game.self_uin = "999"
    admins = {"414744169": {"909736102"}}
    G = "414744169"

    def claim(bill, total, recv, claims_list, rate=20):
        game.handle_claim({"group_id": G, "bill_no": bill, "sender_uin": "909736102",
                           "total_num": total, "recv_num": recv,
                           "rate_permille": rate, "claims": claims_list})

    # 1) 开局 → 甲领部分：不自动收尾、首红包记账、未封盘不判作废
    game.start_round(G, "hongbaojifen_00000900", "复合玩法")
    claim("B1", 3, 1, [{"qq": "111", "name": "甲", "amount": 0.66}], rate=35)
    check("部分领取不触发结算/播报", not settled and not announces)
    check("首红包已记 + 费率已记",
          game.announced[G]["first_bill"]["bill"] == "B1"
          and game.announced[G]["rate_permille"] == 35)
    check("未封盘不作废", G in game.announced)
    # 2) 表格：甲 0.66→0.77；乙（未抢）补值 0.55；超范围拒绝
    err = game.set_claim_amount(G, "111", 0.77, "甲")
    check("改值合法", err is None, str(err))
    err = game.set_claim_amount(G, "111", 1.50)
    check("超范围拒绝", err is not None)
    err = game.set_claim_amount(G, "222", 0.55, "乙")
    check("未抢补值合法", err is None, str(err))
    edit_msgs = [e["msg"] for e in game.announced[G]["events"]]
    check("改/补开奖进回放事件（delta0）",
          any("管理员改开奖金额" in m for m in edit_msgs)
          and any("管理员补值开奖" in m for m in edit_msgs), str(edit_msgs))
    # 3) 结算第一次上报失败 → 保留待重试、不播报不清局
    r = game.settle_round_now(G)
    check("首次结算失败 ok=False", r["ok"] is False and r.get("error"), str(r))
    check("失败未播报未清局", not announces and G in game.announced)
    check("失败进 pending_announced", "hongbaojifen_00000900" in game.pending_announced)
    # 4) 重试成功 → 播报 + 清局 + 通知玩法；改值 0.77 以 delta 4 参与上传；再结算被拒
    game.retry_pending()
    check("重试成功播报", announces and "结算详情" in announces[-1][1], str(announces))
    check("重试后清局", G not in game.announced)
    check("重试后通知玩法", ended and ended[-1] == G, str(ended))
    check("改值 0.77 参与结算", settled and any(
        ev["delta"] == 4 for ev in settled[-1][2]), str(settled))
    r = game.settle_round_now(G)
    check("已结束局再结算被拒", r["ok"] is False, str(r))
    # 5) 封盘后首红包不足 → 立即作废（A3；total=1 < need=2）
    game.start_round(G, "hongbaojifen_00000901", "复合玩法")
    game.seal_round(G)
    claim("B2", 1, 1, [{"qq": "111", "name": "甲", "amount": 0.66}])
    check("份数不足即作废", G not in game.announced, str(game.announced))
    check("作废播报含原因", announces and "少于本局需开奖人数" in announces[-1][1], str(announces))
    # 6) 只认第 1 个红包：B4 第 2 个红包整体忽略；同单号重复推送按 qq 幂等
    game.start_round(G, "hongbaojifen_00000902", "复合玩法")
    claim("B3", 2, 1, [{"qq": "111", "name": "甲", "amount": 0.66}])
    claim("B4", 2, 2, [{"qq": "111", "name": "甲", "amount": 0.66},
                       {"qq": "222", "name": "乙", "amount": 0.55}])
    ann = game.announced[G]
    check("只认首红包单号", ann["first_bill"]["bill"] == "B3", str(ann["first_bill"]))
    check("第2红包 claims 未记", "222" not in ann["claims"], str(ann["claims"]))
    claim("B3", 2, 2, [{"qq": "111", "name": "甲", "amount": 0.66}])
    check("同单号推送 qq 幂等", len(ann["claims"]) == 1)
    game.void_round(G, "管理员手动作废")
    check("手动作废清局", G not in game.announced)
    # 7) 领完 → ready → 手动结算成功（rate 沿用首红包写入值）
    game.start_round(G, "hongbaojifen_00000903", "复合玩法")
    game.seal_round(G)
    claim("B5", 2, 2, [{"qq": "111", "name": "甲", "amount": 0.66},
                       {"qq": "222", "name": "乙", "amount": 0.55}])
    check("领完置 ready", game.announced[G].get("ready_to_settle") is True)
    r = game.settle_round_now(G)
    check("正常结算 ok + results", r.get("ok") is True and r.get("results"),
          str(r)[:200])
    check("结算上报含两玩家", settled and settled[-1][0] == "hongbaojifen_00000903"
          and len([e for e in settled[-1][2] if e["delta"] != 0]) >= 1)
    check("结算后清局", G not in game.announced)
    # 8) 机器人自己在开始本局模式下领取不计入；传统单红包(rounds，无 announced)不回归；
    #    机器人自己/非管理员不产生额外上报（只经 settle 桩观察，不依赖内部结构）
    game.start_round(G, "hongbaojifen_00000904", "复合玩法")
    claim("B6", 2, 2, [{"qq": "999", "name": "机器人自己", "amount": 0.50},
                       {"qq": "111", "name": "甲", "amount": 0.66}])
    check("机器人自己领取不入本局 claims", "999" not in game.announced[G]["claims"])
    game.seal_round(G)
    claim("B6", 2, 2, [{"qq": "222", "name": "乙", "amount": 0.55}])  # 同单号补推：乙补记、甲幂等
    check("领完可结算", game.announced[G].get("ready_to_settle") is True)
    r = game.settle_round_now(G)
    check("上报含 甲(111)+乙(222)", r["ok"] is True and len(r["results"]) == 2, str(r)[:200])
    n0 = len(settled)
    game.handle_claim({"group_id": G, "bill_no": "B8", "sender_uin": "909736102",
                       "total_num": 1, "recv_num": 1,
                       "claims": [{"qq": "111", "name": "甲", "amount": 0.66}]})
    check("传统模式仍自动收尾上报", settled and settled[-1][0] == "B8")
    game.handle_claim({"group_id": G, "bill_no": "B8", "sender_uin": "909736102",
                       "total_num": 1, "recv_num": 1,
                       "claims": [{"qq": "999", "name": "机器人自己", "amount": 0.50}]})
    game.handle_claim({"group_id": G, "bill_no": "B10", "sender_uin": "55555555",
                       "total_num": 1, "recv_num": 1,
                       "claims": [{"qq": "111", "name": "甲", "amount": 0.66}]})
    check("机器人/非管理员不产生额外上报", len(settled) == n0 + 1, str(settled))

    print(f"selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
