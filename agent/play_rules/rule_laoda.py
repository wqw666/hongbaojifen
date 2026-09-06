"""玩法5 抢老大（庄家制擂台）——上传到总后台「会员玩法管理」后启用。

流程：
  1. 管理员「开始本局」（GUI 按钮；也可群内发「开始游戏」）→ 进入下注期
  2. 玩家发「抢老大」→ 先到先得成为**老大（擂主）**，发红包前不能再换人
  3. 其余玩家发「下注N」→ 押注挑战老大（累计名单回复，逐人追加；老大不用下注）
  4. 管理员发红包，参与玩家抢红包 → 每人点数 = 红包金额各位数之和
     （发红包的人抢不了自己的包：老大若是发红包者，点数按 0 —— 必输光，慎抢）
  5. 红包领完 / 挑战者都已领 → 自动结算（settle_redpacket）→ @全体 播报逐人结果

结算规则（庄家制，1:1，无抽水）：
  - 点数 < 老大：挑战者输自己下注额（积分给老大）
  - 点数 > 老大：挑战者赢得自己下注额（老大赔付，1:1）
  - 点数 = 老大：同点组与老大打平 → 整组盈亏 0，组内按注额比例分摊 = 各退回下注
  - 老大净得 = Σ输者下注 − Σ赢者赔付（可为负：赔不起由总后台按余额不足处理）
  - 未抢红包的挑战者点数按 0 计（必输给老大）；老大不抢红包点数按 0（全赔）

开局协议：玩法可选 handle_round_start(group_id, round_id)/handle_round_end(group_id)
（agent「开始本局/结束本局」自动调用）——本玩法借此进入/退出下注期、拿到本局局号；
下注回复的「本局 {局号}」来自 handle_round_start 传入的 round_id。可选
bettor_qqs(group_id) 返回**挑战者** QQ 集合（不含老大：老大不抢包=0 分照常结算，
避免老大是发红包管理员时局永远收不了尾）。可选 handle_round_abort(group_id)：
下注期点「结束本局」= 提前终止——返回公告文本，agent 只播报「积分已退还，不抽水」
并作废本局、不上报（下注期积分从未扣除）。
"""
import re

BETS = {}                 # qq -> {"nickname", "amount", "score"}（挑战者押注）
BOSS = {"qq": None, "nickname": ""}  # 当前局老大（擂主）
PHASE = {"mode": "idle"}  # idle / betting / settled
CURRENT_ROUND = {}        # group_id(int) -> 本局局号（GUI「开始本局」产生）


def _digits(v):
    s = f"{float(v or 0):.2f}".replace(".", "")
    return sum(int(ch) for ch in s if ch.isdigit())


def _round_head(group_id):
    rid = CURRENT_ROUND.get(int(group_id), "")
    return f"本局 {rid}，" if rid else ""


def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    qq = str(qq)
    if t in ("开始游戏", "开局"):
        _reset(int(group_id))
        PHASE["mode"] = "betting"
        return ("游戏开始！玩法：抢老大。发「抢老大」当擂主，其余玩家发「下注N」押注，"
                "管理员发红包比点数：点数小的输给老大，点数大的赢得押注（1:1）。")
    if t == "抢老大":
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员开始本局"
        if BOSS.get("qq"):
            return f"{BOSS['nickname']} 已是本局老大，不能重复抢（点数输赢按他算）"
        BOSS["qq"] = qq
        BOSS["nickname"] = nickname or f"用户{qq}"
        return (f"🎤 {BOSS['nickname']} 抢到老大！其余玩家发「下注N」押注挑战，"
                "管理员发红包定点数：比老大小的输，比老大的赢得押注")
    m = re.match(r"^下注\s*(\d+)$", t)
    if m:
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员开始本局"
        if not BOSS.get("qq"):
            return "还没人当老大：发「抢老大」先抢擂主，再下注挑战"
        if qq == BOSS["qq"]:
            return "你是本局老大，坐庄不用下注，发红包开打即可"
        if qq in BETS:
            return "你已经押注过了，等待开奖"
        amount = int(m.group(1))
        if amount <= 0:
            return "下注金额需大于0"
        BETS[qq] = {"nickname": nickname or f"用户{qq}", "amount": amount, "score": 0}
        parts = "，".join(f"{b['nickname']}押注{b['amount']}" for b in BETS.values())
        return (f"{_round_head(group_id)}老大 {BOSS['nickname']}，当前押注：{parts}"
                if parts else f"{_round_head(group_id)}老大 {BOSS['nickname']} 等挑战者押注")
    return None


def handle_round_start(group_id, round_id=""):
    """agent「开始本局」→ 开局：收下局号、清空上局、进入下注期（与群内发「开始游戏」等价）。"""
    _reset(group_id)  # 清上局（同群局号一并清，防泄漏）
    if round_id:
        CURRENT_ROUND[int(group_id)] = str(round_id).strip()
    PHASE["mode"] = "betting"


def handle_round_end(group_id):
    """agent「结束本局」/红包领完自动收尾 → 回待机、清整局状态，防止泄漏到下一局。"""
    _reset(group_id)


def handle_round_abort(group_id):
    """agent 在下注期点「结束本局」→ 提前终止问询。

    已有人抢老大/押注：返回 @全体 终止公告（积分从未扣除，「退还」即无操作；不抽水、
    不上报）。空局（无人抢无人押）：返回 None，交给 agent 静默取消。"""
    if PHASE["mode"] != "betting" or not (BOSS.get("qq") or BETS):
        return None
    rid = CURRENT_ROUND.get(int(group_id), "")
    head = f"本局 {rid} " if rid else "本局 "
    return f"{head}已终止（积分已退还，不抽水），请等待管理员重新开局。"


def bettor_qqs(group_id):
    """挑战者 QQ 列表（不含老大：老大的点数靠抢包，没抢到按 0 结算，不阻塞收尾）。"""
    if PHASE["mode"] != "betting":
        return []
    return list(BETS.keys())


def handle_redpacket(group_id, qq, nickname, amount):
    """领取事件：记点数并即时@回复领取结果（结算在 settle_redpacket）。"""
    if PHASE["mode"] != "betting":
        return None, 0
    a = float(amount or 0)
    if a <= 0:
        return None, 0
    qq = str(qq)
    pts = _digits(a)
    if qq == BOSS.get("qq"):
        return (f"抢到{a:.2f}元，点数{pts}（你是老大，点数定输赢）", 0)
    if qq in BETS:
        BETS[qq]["score"] = pts
        return (f"抢到{a:.2f}元，点数{pts}（押注{BETS[qq]['amount']}，"
                "本包领完自动开奖结算）", 0)
    return (f"抢到{a:.2f}元，点数{pts}（未押注不计分，发「下注N」挑战老大）", 0)


def settle_redpacket(group_id, claims, rate_permille=20):
    """红包领完 → 庄家制结算。返回 {events:[{qq,nickname,reply,delta}...], announce}。"""
    if PHASE["mode"] != "betting" or not BOSS.get("qq") or not BETS:
        return None
    PHASE["mode"] = "settled"
    boss_qq = str(BOSS["qq"])
    # 记挑战者点数（没抢到 = 0）
    claimed = {}
    for c in claims or []:
        qq = str(c.get("qq") or "")
        if qq:
            claimed[qq] = _digits(c.get("amount"))
    for qq, info in BETS.items():
        info["score"] = claimed.get(qq, 0)
    boss_pts = claimed.get(boss_qq, 0)

    events = []
    lines = []
    boss_delta = 0
    for qq, info in BETS.items():
        amount, pts, nick = info["amount"], info["score"], info["nickname"]
        if pts < boss_pts:            # 输给老大
            delta, verb = -amount, f"输给老大 {amount} 积分"
        elif pts > boss_pts:          # 赢得押注（老大赔付 1:1）
            delta, verb = amount, f"赢得 {amount} 积分"
        else:                         # 同点组与老大打平 → 退回
            delta, verb = 0, "与老大点数相同，打平退回"
        boss_delta -= delta
        events.append({"qq": qq, "nickname": nick,
                       "reply": f"点数 {pts} {'<' if pts < boss_pts else '>' if pts > boss_pts else '='} 老大 {boss_pts}：{verb}（押注{amount}）",
                       "delta": delta})
        lines.append(f"{nick} 押注{amount} 点数{pts} {verb}")
    pool = sum(b["amount"] for b in BETS.values())
    bsign = f"+{boss_delta}" if boss_delta >= 0 else str(boss_delta)
    lost_sum = sum(b["amount"] for b in BETS.values() if b["score"] < boss_pts)
    won_sum = sum(b["amount"] for b in BETS.values() if b["score"] > boss_pts)
    boss_reply = f"点数 {boss_pts}：收输家 {lost_sum}，赔赢家 {won_sum}，净得 {bsign} 积分"
    events.append({"qq": boss_qq, "nickname": BOSS["nickname"], "reply": boss_reply,
                   "delta": boss_delta})
    announce = (f"本局开奖（抢老大）！老大 {BOSS['nickname']} 点数 {boss_pts}，"
                f"挑战押注总额 {pool}：\n" + "\n".join(lines)
                + f"\n老大净得 {bsign} 积分")
    return {"events": events, "announce": announce}


def _reset(group_id=None):
    """回待机并清整局状态（开局/收尾共用）；给出群号时顺带清该群局号。"""
    PHASE["mode"] = "idle"
    BETS.clear()
    BOSS["qq"] = None
    BOSS["nickname"] = ""
    if group_id is not None:
        CURRENT_ROUND.pop(int(group_id), None)


def _selftest() -> int:
    """本地自测：定庄/押注/抢包记点/输赢平结算/老大净得/收尾清理/终止问询。"""
    ok = 0

    def check(name, cond, detail=""):
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    def get(res):
        return {e["qq"]: e for e in res["events"]}

    # 开赛：甲抢老大，乙再抢被拒
    _reset()
    handle_round_start(1, "hongbaojifen_00000500")
    r = handle_message(1, 100, "甲", "抢老大")
    check("甲抢到老大", r and "甲" in r and "抢到老大" in r and BOSS["qq"] == "100", f"{r!r}")
    r = handle_message(1, 200, "乙", "抢老大")
    check("已有老大不能再抢", r and "已是本局老大" in r and BOSS["qq"] == "100", f"{r!r}")
    # 老大本人不用下注；挑战者押注累计名单
    r = handle_message(1, 100, "甲", "下注500")
    check("老大下注被拒", r and "坐庄不用下注" in r, f"{r!r}")
    r = handle_message(1, 200, "乙", "下注100")
    check("乙押注100 回复带局号+老大", r == "本局 hongbaojifen_00000500，老大 甲，当前押注：乙押注100", f"{r!r}")
    r = handle_message(1, 300, "丙", "下注200")
    check("丙押注200 累计名单", r == "本局 hongbaojifen_00000500，老大 甲，当前押注：乙押注100，丙押注200", f"{r!r}")
    check("bettor_qqs 只含挑战者（不含老大）", set(map(str, bettor_qqs(1))) == {"200", "300"},
         str(bettor_qqs(1)))
    # 抢包即时记点：老大与挑战者不同提示；路人提示不计分
    r, d = handle_redpacket(1, 100, "甲", 0.08)   # 甲 点8
    check("老大抢包回复点数", d == 0 and r and "你是老大" in r, f"{r!r},{d}")
    r, d = handle_redpacket(1, 200, "乙", 1.11)   # 乙 点3 → 输
    check("挑战者抢包记点", d == 0 and r and "点数3" in r and BETS["200"]["score"] == 3, f"{r!r}")
    r, d = handle_redpacket(1, 999, "路人", 0.30)
    check("路人抢包提示不计分", d == 0 and r and "未押注" in r and "999" not in BETS, f"{r!r}")
    # 结算：甲8，乙3输100，丙0.91点10赢200 → 老大收100赔200 净-100
    res = settle_redpacket(1, [{"qq": "100", "amount": 0.08}, {"qq": "200", "amount": 1.11},
                               {"qq": "300", "amount": 0.91}])
    ev = get(res)
    check("乙点3<8 输100", ev["200"]["delta"] == -100, str(ev["200"]))
    check("丙点10>8 赢200", ev["300"]["delta"] == 200, str(ev["300"]))
    check("老大净-100（收100赔200）", ev["100"]["delta"] == -100
         and "净得 -100" in ev["100"]["reply"], str(ev["100"]))
    check("播报含逐行输赢", "输" in res["announce"] and "赢" in res["announce"], res["announce"])

    # 平点局：老大0.15点6；乙1.11点3输；丙0.15点6打平退回；丁0.91点10赢
    _reset()
    handle_round_start(1)
    handle_message(1, 100, "甲", "抢老大")
    handle_message(1, 200, "乙", "下注100")
    handle_message(1, 300, "丙", "下注200")
    handle_message(1, 400, "丁", "下注300")
    res = settle_redpacket(1, [{"qq": "100", "amount": 0.15}, {"qq": "200", "amount": 1.11},
                               {"qq": "300", "amount": 0.15}, {"qq": "400", "amount": 0.91}])
    ev = get(res)
    check("平点局：乙点3<6 输100", ev["200"]["delta"] == -100, str(ev["200"]))
    check("平点局：丙点6=6 打平退回", ev["300"]["delta"] == 0 and "打平退回" in ev["300"]["reply"], str(ev["300"]))
    check("平点局：丁点10>6 赢300", ev["400"]["delta"] == 300, str(ev["400"]))
    check("平点局：老大收100赔300 净-200", ev["100"]["delta"] == -200, str(ev["100"]))
    check("打平者在播报出现", "打平退回" in res["announce"], res["announce"])

    # 老大没抢红包=0分 → 挑战者全赢，老大全赔（发红包者抢不了自己的包也在此列）
    _reset()
    handle_round_start(1)
    handle_message(1, 100, "甲", "抢老大")
    handle_message(1, 200, "乙", "下注100")
    res = settle_redpacket(1, [{"qq": "200", "amount": 1.11}])  # 只有乙领了
    ev = get(res)
    check("老大未抢0分：乙赢100", ev["200"]["delta"] == 100, str(ev["200"]))
    check("老大未抢0分：净-100", ev["100"]["delta"] == -100, str(ev["100"]))

    # 待机期行为 + 无老大不能下注 + 终止问询
    _reset()
    handle_round_start(1)
    r = handle_message(1, 200, "乙", "下注100")
    check("无老大押注被拒", r and "还没人当老大" in r, f"{r!r}")
    ab = handle_round_abort(1)
    check("空局终止返回 None", ab is None, f"{ab!r}")
    handle_message(1, 200, "乙", "抢老大")
    handle_message(1, 300, "丙", "下注50")
    ab = handle_round_abort(1)
    check("下注期终止返回公告", ab and "已终止" in ab and "积分已退还" in ab and "不抽水" in ab, f"{ab!r}")
    check("终止只问询不动状态", PHASE["mode"] == "betting" and len(BETS) == 1, str(PHASE))
    handle_round_end(1)
    check("收尾清状态回待机", PHASE["mode"] == "idle" and not BOSS.get("qq") and not BETS)
    check("待机期终止返回 None", handle_round_abort(1) is None)
    r, d = handle_redpacket(1, 200, "乙", 1.11)
    check("待机期抢包不回复", r is None and d == 0)
    r = handle_message(1, 400, "丁", "抢老大")
    check("待机期抢老大提示未开局", r and "未开局" in r, f"{r!r}")
    r = handle_message(1, 400, "丁", "开始游戏")
    check("群内开始游戏进入下注期", r and "开始" in r and PHASE["mode"] == "betting", f"{r!r}")
    res = settle_redpacket(1, [{"qq": "200", "amount": 1.11}])
    check("无人抢老大领完不结算", res is None)

    print(f"rule_laoda selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
