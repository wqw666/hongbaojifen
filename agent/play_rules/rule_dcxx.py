"""玩法4 大吃小（杀小赔大）——上传到总后台「会员玩法管理」后启用。

流程：
  1. 管理员发「开始游戏」→ 进入下注阶段（回复规则说明）
  2. 玩家发「下注N」→ 回复「下注N，剩余积分{balance}」（agent 自动查总后台积分填充）
  3. 管理员发红包，参与玩家抢红包 → 每人点数 = 红包金额各位数之和
  4. 红包领完 → 自动结算（settle_redpacket）→ @全体 播报结果并逐人回复盈亏

结算规则（杀小赔大）：
  - 每人先拿回本金；按点数从高到低，除点数最低者外各「吃」一份与自己下注等额的
    钱——从点数最低者的本金开始往上吃（大吃小，没收够再吃次小……）
  - 抽水 = 总下注 × 游戏费率（千分比，agent 玩法页配置），由点数最低的剩余者承担
  - 点数相同视为同一组，按各自下注比例共同承担盈亏
  - 未抢红包的下注者点数按 0 计（排最后，全赔）
"""
import re

BETS = {}                 # qq -> {"nickname", "amount", "score"}
PHASE = {"mode": "idle"}  # idle / betting / settled


def _digits(v):
    s = f"{float(v or 0):.2f}".replace(".", "")
    return sum(int(ch) for ch in s if ch.isdigit())


def handle_message(group_id, qq, nickname, text):
    t = (text or "").strip()
    if t in ("开始游戏", "开局"):
        BETS.clear()
        PHASE["mode"] = "betting"
        return ("游戏开始！玩法：大吃小。发送「下注N」参与（N=积分），"
                "一分钟后管理员发红包开奖：红包金额各位数之和定大小，"
                "点数大者吃小者，抽水按游戏费率扣除。")
    m = re.match(r"^下注\s*(\d+)$", t)
    if m:
        if PHASE["mode"] != "betting":
            return "当前未开局，请等管理员发「开始游戏」"
        amount = int(m.group(1))
        if amount <= 0:
            return "下注金额需大于0"
        if str(qq) in BETS:
            return "你已经下过注了，等待开奖"
        BETS[str(qq)] = {"nickname": nickname or str(qq), "amount": amount, "score": 0}
        return f"下注{amount}，剩余积分{{balance}}"
    return None


def handle_redpacket(group_id, qq, nickname, amount):
    # 领取事件：记录点数（结算在 settle_redpacket 统一进行）
    if PHASE["mode"] == "betting" and str(qq) in BETS:
        BETS[str(qq)]["score"] = _digits(amount)
    return None, 0


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

    print(f"rule_dcxx selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
