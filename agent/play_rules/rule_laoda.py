"""玩法5 抢庄（庄家制擂台；曾用名「抢老大」）——上传到总后台「会员玩法管理」后启用。

流程：
  1. 管理员「开始本局」（GUI 按钮；也可群内发「开始游戏」）→ 进入下注期
  2. 玩家发「抢庄」（兼容旧词「抢老大」）→ 先到先得成为**庄家（擂主）**，发红包前不能再换
  3. 其余玩家直接发数字（如 500）即押注挑战庄家（旧词「下注500」仍兼容；累计名单
     回复，逐人追加；庄家本人不用下注）
  4. 管理员发红包，参与玩家抢红包 → 每人点数 = 红包金额各位数之和
  5. 红包领完 / 挑战者都已领 → 自动结算（settle_redpacket）→ @全体 播报逐人结果

结算规则（庄家制，1:1，无抽水）：
  - 点数 < 庄家：挑战者输自己押注额（积分给庄家）
  - 点数 > 庄家：挑战者赢得自己押注额（庄家赔付，1:1）
  - 点数 = 庄家：同点组与庄家打平 → 整组盈亏 0，组内按注额比例分摊 = 各退回押注
  - 下注的人没抢红包：点数按 0 **必输光押注**（不参与打平退回）
  - 庄家没抢红包点数按 0 作基准：抢到包的挑战者全赢他；同样没抢的输给他
  - 庄家净得 = Σ输者押注 − Σ赢者赔付（可为负：赔不起由总后台按余额不足处理）

开局协议：玩法可选 handle_round_start(group_id, round_id)/handle_round_end(group_id)
（agent「开始本局/结束本局」自动调用）——本玩法借此进入/退出下注期、拿到本局局号；
下注回复的「本局 {局号}」来自 handle_round_start 传入的 round_id。可选
bettor_qqs(group_id) 返回**挑战者** QQ 集合（不含庄家：庄家点数靠抢包，没抢按 0
作基准照常结算，不阻塞收尾）。可选 handle_round_abort(group_id)：下注期点「结束本局」
= 提前终止——返回公告文本，agent 只播报「积分已退还，不抽水」并作废本局、不上报
（下注期积分从未扣除）。
"""
import re

BETS = {}                 # qq -> {"nickname", "amount", "score"}（挑战者押注）
BOSS = {"qq": None, "nickname": ""}  # 当前局庄家（擂主）
PHASE = {"mode": "idle"}  # idle / betting / settled
CURRENT_ROUND = {}        # group_id(int) -> 本局局号（GUI「开始本局」产生）


def _digits(v):
    s = f"{float(v or 0):.2f}".replace(".", "")
    return sum(int(ch) for ch in s if ch.isdigit())


def _round_head(group_id):
    rid = CURRENT_ROUND.get(int(group_id), "")
    return f"本局 {rid}，" if rid else ""


def _register_bet(group_id, qq, nickname, amount):
    """登记一笔挑战押注并返回回复文本（成功=庄家+累计名单；失败=原因）。"""
    if amount <= 0:
        return "下注金额需大于0"
    if qq in BETS:
        return "你已经押注过了，等待开奖"
    BETS[qq] = {"nickname": nickname or f"用户{qq}", "amount": amount, "score": 0}
    parts = "，".join(f"{b['nickname']}押注{b['amount']}" for b in BETS.values())
    return (f"{_round_head(group_id)}庄家 {BOSS['nickname']}，当前押注：{parts}"
            if parts else f"{_round_head(group_id)}庄家 {BOSS['nickname']} 等挑战者押注")


def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    qq = str(qq)
    if t in ("开始游戏", "开局"):
        _reset(int(group_id))
        PHASE["mode"] = "betting"
        return ("游戏开始！玩法：抢庄。发「抢庄」当庄家，其余玩家直接发数字押注"
                "（如 500），管理员发红包比点数：点数小的押注输给庄家，"
                "点数大的赢得押注（1:1），没抢红包按 0 必输光。")
    if t in ("抢庄", "抢老大"):
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员开始本局"
        if BOSS.get("qq"):
            return f"{BOSS['nickname']} 已是本局庄家，不能重复抢庄（点数输赢按他算）"
        BOSS["qq"] = qq
        BOSS["nickname"] = nickname or f"用户{qq}"
        return (f"🎤 {BOSS['nickname']} 抢到庄家！其余玩家直接发数字押注挑战，"
                "管理员发红包定点数：比庄家小的押注输，比庄家大的赢得押注"
                "（没抢红包按 0 必输光）")
    m = re.match(r"^下注\s*(\d+)$", t)
    if m:
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员开始本局"
        if not BOSS.get("qq"):
            return "还没人抢庄：发「抢庄」先当庄家，再下注挑战"
        if qq == BOSS["qq"]:
            return "你是本局庄家，坐庄不用下注，发红包开打即可"
        return _register_bet(group_id, qq, nickname, int(m.group(1)))
    if t.isdigit():
        # 纯数字 = 直接押注（下注期有效）；不在下注期或无庄家时当作闲聊静默，不打扰报数字
        if PHASE["mode"] != "betting" or not BOSS.get("qq"):
            return None
        if qq == BOSS["qq"]:
            return "你是本局庄家，坐庄不用下注，发红包开打即可"
        return _register_bet(group_id, qq, nickname, int(t))
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

    已有人抢庄/押注：返回 @全体 终止公告（积分从未扣除，「退还」即无操作；不抽水、
    不上报）。空局（无人抢庄无人押注）：返回 None，交给 agent 静默取消。"""
    if PHASE["mode"] != "betting" or not (BOSS.get("qq") or BETS):
        return None
    rid = CURRENT_ROUND.get(int(group_id), "")
    head = f"本局 {rid} " if rid else "本局 "
    return f"{head}已终止（积分已退还，不抽水），请等待管理员重新开局。"


def bettor_qqs(group_id):
    """挑战者 QQ 列表（不含庄家：庄家点数靠抢包，没抢按 0 作基准结算，不阻塞收尾）。"""
    if PHASE["mode"] != "betting":
        return []
    return list(BETS.keys())


def betting_open(group_id):
    """当前是否处于可下注期（agent 下注预检问询：只有下注期才把纯数字拦下校验）。"""
    return PHASE["mode"] == "betting"


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
        return (f"抢到{a:.2f}元，点数{pts}（你是庄家，点数定输赢）", 0)
    if qq in BETS:
        BETS[qq]["score"] = pts
        return (f"抢到{a:.2f}元，点数{pts}（押注{BETS[qq]['amount']}，"
                "本包领完自动开奖结算）", 0)
    return (f"抢到{a:.2f}元，点数{pts}（未押注不计分，直接发数字押注挑战庄家）", 0)


def settle_redpacket(group_id, claims, rate_permille=20):
    """红包领完 → 庄家制结算。返回 {events:[{qq,nickname,reply,delta}...], announce}。"""
    if PHASE["mode"] != "betting" or not BOSS.get("qq") or not BETS:
        return None
    PHASE["mode"] = "settled"
    boss_qq = str(BOSS["qq"])
    # 谁抢到红包、点数多少（抢到金额必 >=0.01 → 点数 >=1；没抢到 = 不在 claimed）
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
    for qq, info in BETS.items():
        amount, pts, nick = info["amount"], info["score"], info["nickname"]
        if qq not in claimed:
            # 下注没抢红包：点数按 0 必输光（不参与同点打平退回）
            delta = -amount
            reply = f"没抢红包点数按 0：押注 {amount} 输给庄家"
            events.append({"qq": qq, "nickname": nick, "reply": reply, "delta": delta})
            lines.append(f"{nick} 押注{amount} 没抢红包点数0 → 输给庄家 {amount} 积分")
            continue
        if pts < boss_pts:            # 输给庄家
            delta, verb = -amount, f"输给庄家 {amount} 积分"
        elif pts > boss_pts:          # 赢得押注（庄家赔付 1:1）
            delta, verb = amount, f"赢得 {amount} 积分"
        else:                         # 抢到且同点：同点组与庄家打平 → 退回
            delta, verb = 0, "与庄家点数相同，打平退回"
        events.append({"qq": qq, "nickname": nick,
                       "reply": f"点数 {pts} {'<' if pts < boss_pts else '>' if pts > boss_pts else '='} 庄家 {boss_pts}：{verb}（押注{amount}）",
                       "delta": delta})
        lines.append(f"{nick} 押注{amount} 点数{pts} {verb}")
    pool = sum(b["amount"] for b in BETS.values())
    lost_sum = sum(max(0, -e["delta"]) for e in events)
    won_sum = sum(max(0, e["delta"]) for e in events)
    boss_delta = lost_sum - won_sum
    bsign = f"+{boss_delta}" if boss_delta >= 0 else str(boss_delta)
    boss_reply = f"点数 {boss_pts}：收输家 {lost_sum}，赔赢家 {won_sum}，净得 {bsign} 积分"
    events.append({"qq": boss_qq, "nickname": BOSS["nickname"], "reply": boss_reply,
                   "delta": boss_delta})
    announce = (f"本局开奖（抢庄）！庄家 {BOSS['nickname']} 点数 {boss_pts}，"
                f"挑战押注总额 {pool}：\n" + "\n".join(lines)
                + f"\n庄家净得 {bsign} 积分")
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
    """本地自测：抢庄定庄/重复抢庄/押注/抢包记点/输赢平结算/没抢必输/庄家收尾/终止问询。"""
    ok = 0

    def check(name, cond, detail=""):
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    def get(res):
        return {e["qq"]: e for e in res["events"]}

    # 开赛：甲抢庄，乙再抢被拒
    _reset()
    handle_round_start(1, "hongbaojifen_00000500")
    r = handle_message(1, 100, "甲", "抢庄")
    check("甲抢到庄家", r and "甲" in r and "抢到庄家" in r and BOSS["qq"] == "100", f"{r!r}")
    r = handle_message(1, 200, "乙", "抢老大")  # 旧词兼容
    check("已有庄家不能再抢（旧词也提示）", r and "已是本局庄家" in r and BOSS["qq"] == "100", f"{r!r}")
    # 庄家本人不用下注；挑战者直接发数字押注（旧词「下注N」兼容）
    r = handle_message(1, 100, "甲", "500")
    check("庄家发数字下注被拒", r and "坐庄不用下注" in r, f"{r!r}")
    r = handle_message(1, 100, "甲", "下注888")  # 旧词
    check("庄家旧词下注也被拒", r and "坐庄不用下注" in r, f"{r!r}")
    r = handle_message(1, 200, "乙", "100")
    check("乙发数字100押注 回复带局号+庄家", r == "本局 hongbaojifen_00000500，庄家 甲，当前押注：乙押注100", f"{r!r}")
    r = handle_message(1, 300, "丙", "下注200")
    check("丙旧词下注200 累计名单", r == "本局 hongbaojifen_00000500，庄家 甲，当前押注：乙押注100，丙押注200", f"{r!r}")
    check("bettor_qqs 只含挑战者（不含庄家）", set(map(str, bettor_qqs(1))) == {"200", "300"},
         str(bettor_qqs(1)))
    check("下注期 betting_open=True", betting_open(1) is True)
    # 抢包即时记点：庄家与挑战者不同提示；路人提示不计分
    r, d = handle_redpacket(1, 100, "甲", 0.08)   # 甲 点8
    check("庄家抢包回复点数", d == 0 and r and "你是庄家" in r, f"{r!r},{d}")
    r, d = handle_redpacket(1, 200, "乙", 1.11)   # 乙 点3 → 输
    check("挑战者抢包记点", d == 0 and r and "点数3" in r and BETS["200"]["score"] == 3, f"{r!r}")
    r, d = handle_redpacket(1, 999, "路人", 0.30)
    check("路人抢包提示不计分", d == 0 and r and "未押注" in r and "999" not in BETS, f"{r!r}")
    # 结算：甲8，乙3输100，丙0.91点10赢200 → 庄家收100赔200 净-100
    res = settle_redpacket(1, [{"qq": "100", "amount": 0.08}, {"qq": "200", "amount": 1.11},
                               {"qq": "300", "amount": 0.91}])
    ev = get(res)
    check("乙点3<8 输100", ev["200"]["delta"] == -100, str(ev["200"]))
    check("丙点10>8 赢200", ev["300"]["delta"] == 200, str(ev["300"]))
    check("庄家净-100（收100赔200）", ev["100"]["delta"] == -100
         and "净得 -100" in ev["100"]["reply"], str(ev["100"]))
    check("播报含逐行输赢", "输" in res["announce"] and "赢" in res["announce"], res["announce"])

    # 平点局：庄家0.15点6；乙1.11点3输；丙0.15点6打平退回；丁0.91点10赢
    _reset()
    handle_round_start(1)
    handle_message(1, 100, "甲", "抢庄")
    r = handle_message(1, 200, "乙", "100")
    check("平点局 乙发数字100 回复裸名单（无局号不带头）",
         r == "庄家 甲，当前押注：乙押注100", f"{r!r}")
    handle_message(1, 300, "丙", "200")
    handle_message(1, 400, "丁", "300")
    res = settle_redpacket(1, [{"qq": "100", "amount": 0.15}, {"qq": "200", "amount": 1.11},
                               {"qq": "300", "amount": 0.15}, {"qq": "400", "amount": 0.91}])
    ev = get(res)
    check("平点局：乙点3<6 输100", ev["200"]["delta"] == -100, str(ev["200"]))
    check("平点局：丙点6=6 打平退回", ev["300"]["delta"] == 0 and "打平退回" in ev["300"]["reply"], str(ev["300"]))
    check("平点局：丁点10>6 赢300", ev["400"]["delta"] == 300, str(ev["400"]))
    check("平点局：庄家收100赔300 净-200", ev["100"]["delta"] == -200, str(ev["100"]))
    check("打平者在播报出现", "打平退回" in res["announce"], res["announce"])

    # 没抢红包必输光：乙下注没抢 → 输100给庄家（即使庄家也没抢=0，不平局）
    _reset()
    handle_round_start(1)
    handle_message(1, 100, "甲", "抢庄")
    handle_message(1, 200, "乙", "100")
    res = settle_redpacket(1, [{"qq": "100", "amount": 0.15}])  # 只有庄家领了，乙没抢
    ev = get(res)
    check("乙没抢红包 输100给庄家", ev["200"]["delta"] == -100
         and "没抢红包" in ev["200"]["reply"], str(ev["200"]))
    check("庄家收100 净+100", ev["100"]["delta"] == 100, str(ev["100"]))
    # 庄家没抢（0 分基准）：抢到包的挑战者全赢；没抢的输给他
    _reset()
    handle_round_start(1)
    handle_message(1, 100, "甲", "抢庄")
    handle_message(1, 200, "乙", "100")
    handle_message(1, 300, "丙", "200")
    res = settle_redpacket(1, [{"qq": "300", "amount": 1.11}])  # 甲、乙都没抢，丙抢了 3 点
    ev = get(res)
    check("庄家0分+乙没抢：乙输100", ev["200"]["delta"] == -100, str(ev["200"]))
    check("丙点3>0 赢200", ev["300"]["delta"] == 200, str(ev["300"]))
    check("庄家0分净-100（收100赔200）", ev["100"]["delta"] == -100, str(ev["100"]))

    # 待机期行为 + 无庄家不能下注 + 终止问询
    _reset()
    handle_round_start(1)
    r = handle_message(1, 200, "乙", "下注100")
    check("无庄家旧词押注被拒", r and "还没人抢庄" in r, f"{r!r}")
    r = handle_message(1, 200, "乙", "88")
    check("无庄家纯数字当作闲聊静默", r is None and "200" not in BETS, f"{r!r}")
    check("无庄家时 betting_open 仍 True（下注期已开）", betting_open(1) is True)
    ab = handle_round_abort(1)
    check("空局终止返回 None", ab is None, f"{ab!r}")
    handle_message(1, 200, "乙", "抢庄")
    handle_message(1, 300, "丙", "50")
    ab = handle_round_abort(1)
    check("下注期终止返回公告", ab and "已终止" in ab and "积分已退还" in ab and "不抽水" in ab, f"{ab!r}")
    check("终止只问询不动状态", PHASE["mode"] == "betting" and len(BETS) == 1, str(PHASE))
    handle_round_end(1)
    check("收尾清状态回待机", PHASE["mode"] == "idle" and not BOSS.get("qq") and not BETS)
    check("待机期 betting_open=False", betting_open(1) is False)
    check("待机期终止返回 None", handle_round_abort(1) is None)
    r, d = handle_redpacket(1, 200, "乙", 1.11)
    check("待机期抢包不回复", r is None and d == 0)
    r = handle_message(1, 400, "丁", "抢庄")
    check("待机期抢庄提示未开局", r and "未开局" in r, f"{r!r}")
    r = handle_message(1, 400, "丁", "开始游戏")
    check("群内开始游戏进入下注期", r and "开始" in r and PHASE["mode"] == "betting", f"{r!r}")
    res = settle_redpacket(1, [{"qq": "200", "amount": 1.11}])
    check("无人抢庄领完不结算", res is None)

    print(f"rule_laoda selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
