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
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.play_name = play_name
        self.play_id = play_id
        self.rule_handler = rule_handler          # (group_id, qq, nickname, amount) -> (reply|None, delta)
        self.rule_handler_batch = rule_handler_batch  # (group_id, claims, rate) -> {events, announce}|None
        self.send_reply = send_reply              # (group_id, at_qq, text)
        self.send_announce = send_announce        # (group_id, text)
        self.settle = settle                      # (round_id, group_id, events) 失败抛异常
        self.get_admin_qqs = get_admin_qqs        # (group_id) -> set[str]
        self.on_log = on_log                      # (msg) 日志回调
        self.rounds: dict[str, dict] = {}         # bill_no -> 局（未点「开始本局」的传统模式）
        self.pending: dict[str, dict] = {}        # 结算失败待重试
        self.last: dict[str, dict] = {}           # bill_no/round_id -> {ok, error, time}
        self.announced: dict[str, dict] = {}      # 开始本局模式：group_id -> 当前局
        self.pending_announced: dict[str, dict] = {}  # 本局结算失败待重试
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
            # 开始本局模式：所有红包领取记入当前局（领完不自动结算，由「结束本局」统计上报）
            if ann.get("ended"):
                return
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
            # 红包领完：先跑玩法批量结算（大吃小等），再 3 秒后自动结束本局
            if total_num and recv_num and recv_num >= total_num:
                self._apply_batch_settle(gid, ann, payload)
                ann["auto_end_at"] = time.time() + 3
                self._log(f"[红包玩法] 群{gid} 红包已领完（{recv_num}/{total_num}），3 秒后自动结束本局")
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
        ann["ended"] = True
        events = ann["events"]
        if not events:
            del self.announced[gid]
            self._log(f"[红包玩法] 群{gid} 本局无事件，已取消")
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
        """worker 定时调用：到点的自动结束本局（领完自动结算）。"""
        for gid, ann in list(self.announced.items()):
            if ann.get("ended"):
                continue
            due = ann.get("auto_end_at") or 0
            if due and time.time() >= due:
                try:
                    self.end_round(gid)
                except (ExecutorBanned, OperatorDisabled):
                    raise
                except Exception as e:  # noqa: BLE001
                    self._log(f"[红包玩法] 自动结束本局异常: {e}")

    def cancel_rounds(self) -> int:
        """作废所有进行中的本局（终止连续模式时调用：数据不上报、不播报）。"""
        n = len(self.announced)
        if n:
            self.announced.clear()
            self._log(f"[红包玩法] 已作废 {n} 个进行中的本局（数据不上报）")
        return n

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

    print(f"selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
