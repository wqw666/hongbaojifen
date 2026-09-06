"""玩法4 大吃小（杀小赔大）——上传到总后台「会员玩法管理」后启用。

流程：
  1. 管理员「开始本局」（GUI 按钮 / 连续开局；也可群内发「开始游戏」）→ 进入下注阶段
  2. 玩家直接发数字（如 500）即下注 N 积分（旧词「下注500」仍兼容）→ 回复
     「本局 {局号}，{所有已下注玩家名单}」（累计名单，逐人追加）
  3. 管理员发红包，参与玩家抢红包 → 每人点数 = 红包金额各位数之和；
     每个抢到的人立即收到 @回复（金额+点数，未下注者提示不计分）
  4. 红包领完 → 自动结算（settle_redpacket）→ @全体 播报结果并逐人回复盈亏

结算规则（杀小赔大）：
  - 每人先拿回本金；按点数从高到低，除点数最低者外各「吃」一份与自己下注等额的
    钱——从点数最低者的本金开始往上吃（大吃小，没收够再吃次小……）
  - 抽水 = 总下注 × 游戏费率（千分比，agent 玩法页配置），由点数最低的剩余者承担
  - 点数相同视为同一组，按各自下注比例共同承担盈亏
  - 未抢红包的下注者点数按 0 计（排最后，全赔）

开局协议：玩法可选 handle_round_start(group_id, round_id)/handle_round_end(group_id)
（agent「开始本局/结束本局」自动调用）——本玩法借此进入/退出下注期、拿到本局局号，
不必依赖管理员群内发「开始游戏」；下注回复的「本局 {局号}」来自 handle_round_start
传入的 round_id。另有可选 bettor_qqs(group_id)：返回本局已下注者 QQ 集合，供 agent
判定「下注玩家均已领取红包」→ 提前按 10s/10s 分阶段收尾（不必等红包被全部领完）。

终止协议：可选 handle_round_abort(group_id)——agent 在下注期点「结束本局」先问询本玩法：
返回公告文本 → 按「本局已终止（积分已退还，不抽水）」@全体 播报并作废本局、不上报
（下注期积分从未扣除，「退还」即无操作）；返回 None → 走正常结算上报收尾。
"""
import re

BETS = {}                 # qq -> {"nickname", "amount", "score"}
PHASE = {"mode": "idle"}  # idle / betting / settled
CURRENT_ROUND = {}        # group_id(int) -> 本局局号（GUI「开始本局」产生）


def _digits(v):
    s = f"{float(v or 0):.2f}".replace(".", "")
    return sum(int(ch) for ch in s if ch.isdigit())


def _register_bet(group_id, qq, nickname, amount):
    """登记一笔押注并返回回复文本（成功=累计名单；失败=原因）。"""
    if amount <= 0:
        return "下注金额需大于0"
    if str(qq) in BETS:
        return "你已经下过注了，等待开奖"
    BETS[str(qq)] = {"nickname": nickname or str(qq), "amount": amount, "score": 0}
    rid = CURRENT_ROUND.get(int(group_id), "")
    parts = "，".join(
        f"{b['nickname']}下注{b['amount']}" for b in BETS.values())
    return (f"本局 {rid}，{parts}" if rid else parts)


def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    if t in ("开始游戏", "开局"):
        BETS.clear()
        PHASE["mode"] = "betting"
        return ("游戏开始！玩法：大吃小。直接发数字下注（N=积分，如 500），"
                "一分钟后管理员发红包开奖：红包金额各位数之和定大小，"
                "点数大者吃小者，抽水按游戏费率扣除。")
    m = re.match(r"^下注\s*(\d+)$", t)
    if m:
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员发「开始游戏」"
        return _register_bet(group_id, qq, nickname, int(m.group(1)))
    if t.isdigit():
        # 纯数字 = 直接下注（下注期有效）；不在下注期当作闲聊静默，不打扰报数字
        if PHASE["mode"] != "betting":
            return None
        return _register_bet(group_id, qq, nickname, int(t))
    return None


def handle_round_start(group_id, round_id=""):
    """agent「开始本局」→ 开局：收下局号、清空上局、进入下注期（与群内发「开始游戏」等价）。"""
    g = int(group_id)
    if round_id:
        CURRENT_ROUND[g] = str(round_id).strip()
    else:
        CURRENT_ROUND.pop(g, None)  # 兼容旧写法只传 group_id
    BETS.clear()
    PHASE["mode"] = "betting"


def handle_round_abort(group_id):
    """agent 在下注期点「结束本局」→ 提前终止问询。

    下注期有人下注：返回 @全体 终止公告（积分从未扣除，「退还」即无操作；不抽水、
    不上报）。不在下注期或无人下注：返回 None（无可退还，交给 agent 静默取消）。"""
    if PHASE["mode"] != "betting" or not BETS:
        return None
    rid = CURRENT_ROUND.get(int(group_id), "")
    head = f"本局 {rid} " if rid else "本局 "
    return f"{head}已终止（积分已退还，不抽水），请等待管理员重新开局。"


def handle_round_end(group_id):
    """agent「结束本局」/红包领完自动收尾 → 回待机、清局号与下注名单，防止状态泄漏到下一局。"""
    PHASE["mode"] = "idle"
    BETS.clear()
    CURRENT_ROUND.pop(int(group_id), None)


def bettor_qqs(group_id):
    """本局已下注者 QQ 列表（agent 判定「下注玩家均已领取红包」→ 分阶段收尾用）。"""
    if PHASE["mode"] != "betting":
        return []
    return list(BETS.keys())


def betting_open(group_id):
    """当前是否处于可下注期（agent 下注预检问询：只有下注期才把纯数字拦下校验）。"""
    return PHASE["mode"] == "betting"


def handle_redpacket(group_id, qq, nickname, amount):
    """领取事件：记点数并即时@回复领取结果（类似红包计分玩法）；结算在 settle_redpacket。"""
    if PHASE["mode"] != "betting":
        return None, 0
    a = float(amount or 0)
    if a <= 0:
        return None, 0
    qq = str(qq)
    pts = _digits(a)
    if qq in BETS:
        BETS[qq]["score"] = pts
        return (f"抢到{a:.2f}元，点数{pts}（已下注{BETS[qq]['amount']}，"
                "本包领完自动开奖结算）", 0)
    return (f"抢到{a:.2f}元，点数{pts}（未下注不计分，直接发数字参与本局）", 0)


def settle_redpacket(group_id, claims, rate_permille=20):
    """红包领完 → 大吃小结算。返回 {events:[{qq,nickname,reply,delta}...], announce}。"""
    if PHASE["mode"] != "betting" or not BETS:
        return None
    PHASE["mode"] = "settled"
    # 记录每个参与者的点数（未抢到红包 = 0 分）
    for c in claims or []:
        qq = str(c.get("qq") or "")
        if qq in BETS:
            BETS[qq]["score"] = _digits(c.get("amount"))
    return _settle(rate_permille)


def _settle(rate_permille):
    players = [dict(qq=qq, **info) for qq, info in BETS.items()]
    pool = sum(p["amount"] for p in players)
    fee = pool * max(0, min(1000, int(rate_permille or 20))) // 1000

    # 按点数分组（相同点数同组），组内按比例承担盈亏
    players.sort(key=lambda p: -p["score"])
    groups = []
    for p in players:
        if groups and groups[-1]["score"] == p["score"]:
            groups[-1]["members"].append(p)
            groups[-1]["bet"] += p["amount"]
        else:
            groups.append({"score": p["score"], "members": [p], "bet": p["amount"]})

    eaten = {}          # id(group) -> 被吃掉的金额
    win = {id(g): 0 for g in groups}   # id(group) -> 实际赢到的金额

    # 胜者组（除点数最低组外）按点数从高到低依次吃：从最低分组往上吃，只吃排名比自己低的组
    for wg in groups[:-1]:
        need = wg["bet"]
        for lg in reversed(groups):
            if lg is wg:
                break  # 吃到自己为止：不碰排名更高的组
            avail = lg["bet"] - eaten.get(id(lg), 0)
            if avail <= 0:
                continue
            take = min(need, avail)
            eaten[id(lg)] = eaten.get(id(lg), 0) + take
            need -= take
            if need <= 0:
                break
        win[id(wg)] = wg["bet"] - need

    # 抽水：从点数最低的剩余组往上收
    fee_left = fee
    for lg in reversed(groups):
        avail = lg["bet"] - eaten.get(id(lg), 0)
        if avail <= 0:
            continue
        take = min(fee_left, avail)
        eaten[id(lg)] = eaten.get(id(lg), 0) + take
        fee_left -= take
        if fee_left <= 0:
            break

    # 组内按比例分配盈亏，生成事件与播报
    events = []
    lines = []
    for g in groups:
        for p in g["members"]:
            share_win = win[id(g)] * p["amount"] // g["bet"] if g["bet"] else 0
            share_loss = eaten.get(id(g), 0) * p["amount"] // g["bet"] if g["bet"] else 0
            delta = share_win - share_loss
            sign = f"+{delta}" if delta >= 0 else str(delta)
            events.append({
                "qq": p["qq"], "nickname": p["nickname"],
                "reply": (f"开奖点数 {p['score']}：{'赢' if delta >= 0 else '亏'} {sign} 积分"
                          f"（下注{p['amount']}）"),
                "delta": delta,
            })
            lines.append(f"{p['nickname']} 下注{p['amount']} 点数{p['score']} → {sign} 积分")
    announce = (f"本局开奖（大吃小）！总下注 {pool}，抽水 {fee}：\n" + "\n".join(lines))
    return {"events": events, "announce": announce}


def _selftest() -> int:
    """本地自测：3 人与 4 人示例 + 平分。"""
    ok = 0

    def check(name, cond, detail=""):
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    # 4 人示例：A100/B20/C200/D80，1>2>3>4，费率2%
    BETS.clear()
    PHASE["mode"] = "betting"
    BETS["1"] = {"nickname": "A", "amount": 100, "score": 0}
    BETS["2"] = {"nickname": "B", "amount": 20, "score": 0}
    BETS["3"] = {"nickname": "C", "amount": 200, "score": 0}
    BETS["4"] = {"nickname": "D", "amount": 80, "score": 0}
    res = settle_redpacket(1, [
        {"qq": "1", "amount": 0.91}, {"qq": "2", "amount": 0.08},
        {"qq": "3", "amount": 0.33}, {"qq": "4", "amount": 0.05},
    ], rate_permille=20)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    check("4人：A +100", deltas.get("1") == 100, str(deltas))
    check("4人：B +20", deltas.get("2") == 20, str(deltas))
    check("4人：C -48 → 152", deltas.get("3") == -48, str(deltas))
    check("4人：D -80 → 0", deltas.get("4") == -80, str(deltas))
    check("4人：抽水8", "抽水 8" in res["announce"], res["announce"])

    # 3 人示例：A100/B20/C200，1>2>3，费率2%
    BETS.clear()
    PHASE["mode"] = "betting"
    BETS["1"] = {"nickname": "A", "amount": 100, "score": 0}
    BETS["2"] = {"nickname": "B", "amount": 20, "score": 0}
    BETS["3"] = {"nickname": "C", "amount": 200, "score": 0}
    res = settle_redpacket(1, [
        {"qq": "1", "amount": 0.91}, {"qq": "2", "amount": 0.08},
        {"qq": "3", "amount": 0.33},
    ], rate_permille=20)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    check("3人：A +100", deltas.get("1") == 100, str(deltas))
    check("3人：B +20", deltas.get("2") == 20, str(deltas))
    check("3人：C -126 → 74", deltas.get("3") == -126, str(deltas))

    # 平分：两人点数相同 → 按比例分账。A/B 各下注 100，点数相同并列第一（吃 C）
    BETS.clear()
    PHASE["mode"] = "betting"
    BETS["1"] = {"nickname": "A", "amount": 100, "score": 0}
    BETS["2"] = {"nickname": "B", "amount": 100, "score": 0}
    BETS["3"] = {"nickname": "C", "amount": 100, "score": 0}
    res = settle_redpacket(1, [
        {"qq": "1", "amount": 0.91}, {"qq": "2", "amount": 0.91},
        {"qq": "3", "amount": 0.15},
    ], rate_permille=20)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    # C 本金被吃光，抽水 6 由剩余者（A/B 按比例）承担 → 各净 47
    check("平分：A +47", deltas.get("1") == 47, str(deltas))
    check("平分：B +47", deltas.get("2") == 47, str(deltas))
    check("平分：C -100", deltas.get("3") == -100, str(deltas))

    # 局生命周期：GUI「开始本局/结束本局」回调（不依赖群内发「开始游戏」）
    PHASE["mode"] = "idle"
    BETS.clear()
    handle_round_start(1)
    check("handle_round_start 进入下注期", PHASE["mode"] == "betting")
    # 抢红包即时回复：已下注者报点数，未下注者提示（都不直接加分，领完统一结算）
    BETS["1"] = {"nickname": "A", "amount": 100, "score": 0}
    r, d = handle_redpacket(1, 1, "A", 0.10)
    check("下注者抢包即时回复", d == 0 and r and "0.10" in r and "点数1" in r, f"{r!r},{d}")
    check("抢包记录点数", BETS["1"]["score"] == 1, str(BETS["1"]))
    r2, d2 = handle_redpacket(1, 9, "路人", 0.08)
    check("未下注抢包提示不计分", d2 == 0 and r2 and "未下注" in r2 and "9" not in BETS, f"{r2!r},{d2}")
    r3, d3 = handle_redpacket(1, 9, "路人", 0)
    check("金额0不回复", r3 is None and d3 == 0)
    handle_round_end(1)
    check("handle_round_end 回到待机", PHASE["mode"] == "idle")
    r4, d4 = handle_redpacket(1, 1, "A", 0.10)
    check("待机期抢包不回复", r4 is None and d4 == 0)
    # 待机期（未开局）红包领完不结算（防止串局）
    res = settle_redpacket(1, [{"qq": "1", "amount": 0.10}], rate_permille=20)
    check("待机期领完不结算", res is None)

    # 下注累计名单回复：局号来自 handle_round_start(group_id, round_id)
    PHASE["mode"] = "idle"
    BETS.clear()
    CURRENT_ROUND.clear()
    handle_round_start(1, "hongbaojifen_00000099")
    check("双参开局记住局号", PHASE["mode"] == "betting"
         and CURRENT_ROUND.get(1) == "hongbaojifen_00000099", str(CURRENT_ROUND))
    r = handle_message(1, 1, "用户1", "100")
    check("用户1 纯数字下注100 回复本局+首名单", r == "本局 hongbaojifen_00000099，用户1下注100", f"{r!r}")
    r = handle_message(1, 2, "用户2", "200")
    check("用户2 纯数字下注200 回复本局+累计名单",
         r == "本局 hongbaojifen_00000099，用户1下注100，用户2下注200", f"{r!r}")
    check("下注登记进 BETS", BETS.get("1", {}).get("amount") == 100
         and BETS.get("2", {}).get("amount") == 200, str(BETS))
    r = handle_message(1, 6, "用户6", "下注300")  # 旧词兼容
    check("旧词「下注300」仍受理", r == "本局 hongbaojifen_00000099，用户1下注100，用户2下注200，用户6下注300",
         f"{r!r}")
    r = handle_message(1, 1, "用户1", "300")
    check("重复下注拒绝（纯数字）", r and "已经下过注" in r, f"{r!r}")
    check("bettor_qqs 返回下注者集合", set(map(str, bettor_qqs(1))) == {"1", "2", "6"}, str(bettor_qqs(1)))
    check("下注期 betting_open=True", betting_open(1) is True)
    ab = handle_round_abort(1)
    check("下注期终止返回公告（局号+退还+不抽水）", ab and "hongbaojifen_00000099" in ab
         and "已终止" in ab and "积分已退还" in ab and "不抽水" in ab, f"{ab!r}")
    check("终止只问询不动状态（等 handle_round_end 清理）",
         PHASE["mode"] == "betting" and len(BETS) == 3, str(PHASE))
    handle_round_end(1)
    check("收尾清局号回待机", PHASE["mode"] == "idle" and 1 not in CURRENT_ROUND, str(CURRENT_ROUND))
    check("待机期 bettor_qqs 为空", not bettor_qqs(1))
    check("待机期终止返回 None", handle_round_abort(1) is None)
    r = handle_message(1, 3, "用户3", "下注500")
    check("待机期旧词下注回复未开局", r and "未开局" in r, f"{r!r}")
    r = handle_message(1, 3, "用户3", "88")
    check("待机期纯数字当作闲聊静默", r is None and "3" not in BETS, f"{r!r}")
    check("待机期 betting_open=False", betting_open(1) is False)

    # 无局号路径：群内发「开始游戏」开局（CURRENT_ROUND 无局号）→ 回复不带局号前缀
    BETS.clear()
    r = handle_message(1, 4, "路人", "开始游戏")
    check("群内开始游戏进入下注期", r and "开始" in r and PHASE["mode"] == "betting", f"{r!r}")
    check("无人下注时终止返回 None", handle_round_abort(1) is None)
    r = handle_message(1, 5, "新玩家", "50")
    check("无局号纯数字下注回复裸名单", r == "新玩家下注50", f"{r!r}")

    print(f"rule_dcxx selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
