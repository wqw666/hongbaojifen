"""复合玩法（大吃小×撑庄，唯一玩法，协议 v2）。

玩法：默认大吃小；下注期有人发「撑」→ 此人成庄家（先到先得，已下注者不能撑），
本局转撑庄模式。封盘后管理员发红包（每份 0.01~0.99 元，份数 >= 需开奖人数：
无庄 = 下注人数，有庄 = 下注人数 + 1），红包金额定大小；所有人领完由 agent 手动点「结算」，
本文件 settle_redpacket 按大吃小/撑庄算法结算。

积分只经红包结算产生：handle_message/handle_redpacket 均返回 delta 0。

注意：本文件会被引擎热重载，import 即清空 ROUNDS —— 更新玩法文件前请先结束进行中的局。

比大小（两种模式共用，金额取 f"{amount:.2f}" 两位 cents 数字 (a,b)）：
  点数 pts = (a + b) % 10（0.77 -> 7+7=14 -> 4；0.91 -> 9+1=10 -> 0）
  排序键 key = (-pts, -zeros, -hi)，升序排即 点数大优先 -> 含0多优先 -> 最大位大优先；
  三键全同 = 同名次组，组内按下注顺序逐个消化（先下注先拿满/先被扣满，封顶=自己注额）。
  金额不在 (0, 0.99]（或未领取无记录，spec D3）按 0 处理：点数 0、最末名次、会被吃
  （未领按 0 输光，agent 表格会标红提示修正）。

自测：python play_rules/rule_fuhe.py，须全绿。
"""
from __future__ import annotations

import time
import unicodedata

ROUNDS: dict[int, dict] = {}  # group_id -> 局状态

_CMD_START = {"开始游戏", "开局"}
_OPENING = ("游戏开始！本局对局编号：{round_id}。玩法：复合玩法（大吃小×撑庄）："
            "直接发数字下注（如 500，整数积分）；想当庄家的发「撑」即可撑庄（先到先得，本局转撑庄模式）。"
            "下注结束后管理员发红包（每份 0.01~0.99 元，份数不少于需开奖人数），红包金额定大小。")


def _cjk_w(s: str) -> int:
    """显示宽度：CJK/全角按 2 列计（对齐用），其余按 1。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in str(s))


def _table_text(headers: list[str], rows: list[list], right: tuple = ()) -> str:
    """按列对齐的纯文本表格（3 列及以上；right=右对齐的列下标）。"""
    grid = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(_cjk_w(row[c]) for row in grid) for c in range(len(headers))]
    out = []
    for row in grid:
        cells = []
        for c, cell in enumerate(row):
            pad = widths[c] - _cjk_w(cell)
            if pad < 0:
                pad = 0
            cells.append((" " * pad + cell) if c in right else (cell + " " * pad))
        out.append("  ".join(cells).rstrip())
    return "\n".join(out)


def _st(gid: int) -> dict | None:
    return ROUNDS.get(int(gid))


def _open_state(round_id: str = "") -> dict:
    """新开一局（betting, 大吃小）。round_id 为空串时回复文案省略局号。"""
    return {"phase": "betting", "round_id": str(round_id or ""), "mode": "dcxx",
            "bets": [], "boss": None, "sealed_msg": "", "pool": 0, "fee": 0,
            "started": time.time()}


def _opening_text(s: dict) -> str:
    rid = s.get("round_id") or ""
    return _OPENING.format(round_id=rid if rid else "（本局）")


def _ids(s: dict) -> list[dict]:
    return [dict(b) for b in s.get("bets") or []]


def _bet_line_text(s: dict) -> str:
    """下注累计一行式名单：1.昵称：分，2.昵称：分（下注反馈用，紧凑）。"""
    return "，".join(f"{i}.{b['nickname']}：{b['amount']}"
                     for i, b in enumerate(_ids(s), 1))


def _reset(gid: int) -> None:
    ROUNDS.pop(int(gid), None)


# ---------- 金额 -> 点数 / 排序键 ----------

def _cents(amount: float) -> tuple[int, int]:
    """金额(0,0.99] -> (a, b) 两位 cents 数字；无效金额返回 (-1,-1)。"""
    try:
        if not (0 < float(amount) <= 0.99):
            return -1, -1
        s = f"{float(amount):.2f}"            # "0.77" -> 取小数点后两位
        return int(s[2]), int(s[3])
    except Exception:
        return -1, -1


def _pt_key(amount: float) -> tuple[int, int, int] | None:
    """排序键 (-pts, -zeros, -hi)；升序排 = 点数高的在前。无效金额返回 None（垫底）。"""
    a, b = _cents(amount)
    if a < 0:
        return None
    pts = (a + b) % 10
    return (-pts, -(int(a == 0) + int(b == 0)), -max(a, b))


def _pts_text(amount: float) -> str:
    """点数文案（无点显示 -）。pts = -key[0]。"""
    k = _pt_key(amount)
    return "-" if k is None else str(-k[0])


def _pts_of(key) -> int:
    """点数数值；key=None（无效/未领按 0 点）→ 0。"""
    return 0 if key is None else -key[0]


def _cmp_amounts(a: float, b: float) -> int:
    """a 对 b 比较：>0 大、<0 小、0 平（前 3 键全同=平；平局消化见 settle）。"""
    ka, kb = _pt_key(a), _pt_key(b)
    if ka is None and kb is None:
        return 0
    if ka is None:
        return -1
    if kb is None:
        return 1
    return (ka[0] < kb[0]) - (ka[0] > kb[0]) or (ka[1] < kb[1]) - (ka[1] > kb[1]) \
        or (ka[2] < kb[2]) - (ka[2] > kb[2])  # key 越小越优（点数大/含0多/最大位大在前）


# ---------- handle_message：命令与下注 ----------

def handle_message(group_id: int, qq: int, nickname: str, text: str) -> str | None:
    """群消息入口（delta 恒 0；积分只经红包结算产生）。"""
    gid = int(group_id)
    t = (text or "").strip()
    s = _st(gid)
    qq_s, nick = str(qq), str(nickname or "")
    if t in _CMD_START:
        # 开局只认桌面玩法面板「开始本局」（走 handle_round_start，同时建 agent 本局会话）；
        # 群内文字开局会造成规则状态与 agent 会话脱钩，一律引导去面板
        if s is not None:
            return (f"本局（{s.get('round_id') or ''}）进行中：请管理员在玩法面板"
                    f"「结算/作废本局」后，再点「开始本局」开新局")
        return "开局请在桌面玩法面板点「开始本局」（本局由 agent 操作，不认群内文字开局）"
    if s is None or s["phase"] == "idle":
        if t == "撑" or _is_bet_word(t):
            return "当前未开局，等待管理员在面板开始游戏"
        return None
    if s["phase"] == "sealed":
        if t == "撑" or _is_bet_word(t):
            return f"本局已封盘（{s.get('round_id') or ''}），等待开奖，请勿再下注/撑庄。"
        return None
    # betting
    if s.get("boss") and qq_s == s["boss"]["qq"]:
        return "坐庄不用下注" if _is_bet_word(t) else None
    if t == "撑":
        return _claim_boss(gid, s, qq_s, nick)
    m = _bet_match(t)
    if not m:
        return None
    if any(b["qq"] == qq_s for b in s["bets"]):
        return f"您已下注 {int(m)} 积分，本局只能下注一次"
    s["bets"].append({"qq": qq_s, "nickname": nick or f"QQ{qq_s}",
                      "amount": int(m), "ts": time.time()})
    # 紧凑累计名单（只回列表本身：1.甲：100，2.乙：200）
    return _bet_line_text(s)


def _is_bet_word(t: str) -> bool:
    return bool(t and ((t.isdigit() and int(t) >= 1) or t.startswith("下注")))


def _bet_match(t: str) -> str | None:
    """纯数字 / 下注N -> 金额字符串（>=1）；其它 None。"""
    if t.isdigit() and int(t) >= 1:
        return t
    if t.startswith("下注"):
        n = t[2:].strip()
        if n.isdigit() and int(n) >= 1:
            return n
    return None


def _claim_boss(gid: int, s: dict, qq_s: str, nick: str) -> str:
    """发「撑」：先到先得成庄家（A2：已下注者拒绝）。"""
    if s.get("boss"):
        return f"{s['boss']['nickname']} 已是本局庄家，不能重复撑庄"
    if any(b["qq"] == qq_s for b in s["bets"]):
        return "已下注不能撑庄（先下注的按大吃小参与），请等待下一局"
    n = nick or f"QQ{qq_s}"
    s["boss"] = {"qq": qq_s, "nickname": n}
    s["mode"] = "qzz"
    # 庄家现存积分由 agent 用 {balance} 占位符现查现填：给其他人押注合计作参考
    # （封盘时庄家余额 < 下注池+抽水 会直接作废，故提示勿超庄家积分）
    return (f"{n} 撑庄，本局改为撑庄模式，{n} 积分 {{balance}}，"
            f"其余人押注合计勿超庄家积分（超了本局作废）")


# ---------- 局生命周期（协议） ----------

def handle_round_start(group_id: int, round_id: str = "") -> str | None:
    """GUI「开始本局」：重置该群进入 betting。返回开场白（GUI 播报用）。"""
    gid = int(group_id)
    ROUNDS[gid] = _open_state(str(round_id or ""))
    return _opening_text(ROUNDS[gid])


def handle_round_end(group_id: int) -> None:
    """结算成功/关局后清该群状态。"""
    _reset(int(group_id))


def handle_round_abort(group_id: int) -> str | None:
    """「作废本局」问询（旧协议别名，等价 handle_void）。"""
    return handle_void(group_id)


def handle_void(group_id: int) -> str | None:
    """作废（红包不足/余额不足/手动）：返回作废公告并复位状态。无进行中局返回 None。"""
    s = _st(int(group_id))
    if s is None:
        return None
    rid = s.get("round_id") or ""
    _reset(int(group_id))
    return f"本局 {rid or '（本局）'} 已终止/作废（积分未扣），请等待管理员重新开局。"


def betting_open(group_id: int) -> bool:
    s = _st(int(group_id))
    return bool(s and s["phase"] == "betting")


def bettor_qqs(group_id: int) -> list[str]:
    """已下注者 QQ 集合（不含庄家）。"""
    s = _st(int(group_id))
    return [b["qq"] for b in (s or {}).get("bets") or []]


def claim_need(group_id: int) -> int:
    """本局需开奖人数：有庄=下注人数+1（庄家需 1 份定自己点数）；非 betting/sealed=0。"""
    s = _st(int(group_id))
    if not s or s["phase"] not in ("betting", "sealed"):
        return 0
    return len(s.get("bets") or []) + (1 if s.get("boss") else 0)


def bet_snapshot(group_id: int) -> list[dict]:
    """注序下注明细（供 GUI 表格）；有庄末尾追加坐庄行 {qq,nickname,amount:0,boss:True}。"""
    s = _st(int(group_id))
    rows = [dict(b) for b in (s or {}).get("bets") or []]
    if s and s.get("boss"):
        rows.append({"qq": s["boss"]["qq"], "nickname": s["boss"]["nickname"],
                     "amount": 0, "boss": True})
    return rows


def seal_info(group_id: int) -> dict:
    """当前状态快照（GUI 状态栏/按钮门控）。无局返回 idle 快照。"""
    s = _st(int(group_id))
    if s is None:
        return {"phase": "idle", "mode": "dcxx", "round_id": "",
                "boss": None, "n_bets": 0, "pool": 0, "fee": 0, "claim_need": 0}
    return {"phase": s["phase"], "mode": s["mode"], "round_id": s.get("round_id") or "",
            "boss": dict(s["boss"]) if s.get("boss") else None,
            "n_bets": len(s.get("bets") or []),
            "pool": s.get("pool") or 0, "fee": s.get("fee") or 0,
            "claim_need": claim_need(int(group_id))}


# ---------- 封盘 ----------

def handle_seal(group_id: int, rate_permille: int = 20) -> dict:
    """「停止下注」：无人下注 -> 直接作废（ok=False, 状态复位）；否则 phase=sealed 生成汇总。

    返回 {ok, text, banker_check}；撑庄时 banker_check={qq,nickname,need}（GUI 查余额预检）。
    rate_permille 与结算/抽水同一口径（agent 传当前配置费率）。"""
    gid = int(group_id)
    s = _st(gid)
    if s is None:
        return {"ok": False, "text": "当前没有进行中的本局，无法停止下注", "banker_check": None}
    if s["phase"] != "betting":
        return {"ok": False, "text": "本局已封盘或不在下注期", "banker_check": None}
    bets = s.get("bets") or []
    if not bets and not s.get("boss"):
        rid = s.get("round_id") or ""
        _reset(gid)
        return {"ok": False, "text": f"本局 {rid or '（本局）'} 无人下注，本局作废（积分未扣），请重新开局。",
                "banker_check": None}
    s["phase"] = "sealed"
    pool = sum(b["amount"] for b in bets)
    rate = max(0, min(int(rate_permille or 20), 1000))
    fee = pool * rate // 1000
    s["pool"], s["fee"] = pool, fee
    need = claim_need(gid)
    need_tail = (f"，庄家 {s['boss']['nickname']} 需另留 1 份" if s.get("boss") else "")
    table = _table_text(["序号", "用户名称", "下注积分"],
                        [[i, b["nickname"], b["amount"]] for i, b in enumerate(bets, 1)],
                        right=(0, 2))
    text = (f"停止下注，合计{pool}分：\n{table}"
            + f"\n管理员请发红包开奖{need_tail}（每份 0.01~0.99 元，份数不少于 {need} 份）。")
    s["sealed_msg"] = text
    bc = None
    if s.get("boss"):
        bc = {"qq": s["boss"]["qq"], "nickname": s["boss"]["nickname"],
              "need": pool + fee}
    return {"ok": True, "text": text, "banker_check": bc}


# ---------- 红包领取（agent 编排层逐包回调；本文件不存金额，结算时再取） ----------

def handle_redpacket(group_id: int, qq: int, nickname: str, amount: float):
    """红包领取事件：正常领取只静默记账，不 @不回复（delta 0）。

    返回 (reply|None, 0)："" = 已受理记账但不发消息（领取人不再被打扰）；
    None = 未参与本局/不在开奖期（不记账）；金额无效(>0.99)返回警告文本提示修正。
    真正记点数/盈亏在 settle_redpacket 用最终 claims 结算。"""
    s = _st(int(group_id))
    if s is None or s["phase"] not in ("betting", "sealed"):
        return None, 0
    qq_s = str(qq)
    part = [b["qq"] for b in s.get("bets") or []]
    if not (qq_s in part or (s.get("boss") and qq_s == s["boss"]["qq"])):
        return None, 0
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        amt = -1.0
    if not (0 < amt <= 0.99):
        return (f"⚠ 金额 {amt} 异常（红包每份应 0.01~0.99 元），此份不计点数，"
                f"请管理员在开奖表修正或作废本局"), 0
    return "", 0


# ---------- 结算（settle_redpacket：大吃小 / 撑庄） ----------

def settle_redpacket(group_id: int, claims: list[dict], rate_permille: int = 20) -> dict | None:
    """封盘后的最终结算。claims: [{qq, nickname?, amount}]（含表格补值/改值），
    未出现在 claims 的下注者/庄家按 0 计（点数 0）。返回 {events, announce}；无局返回 None。"""
    gid = int(group_id)
    s = _st(gid)
    if s is None:
        return None
    amt_of = {}
    for c in claims or []:
        qq_s = str(c.get("qq") or "")
        if qq_s:
            try:
                amt_of[qq_s] = float(c.get("amount") or 0)
            except (TypeError, ValueError):
                amt_of[qq_s] = 0.0
    if s.get("boss"):
        return _settle_qzz(gid, s, amt_of, rate_permille)
    return _settle_dcxx(gid, s, amt_of, rate_permille)


def _row_amounts(s: dict, amt_of: dict[str, float]) -> list[dict]:
    """结算参与行（按注序）：每行 {qq, nickname, stake(注额), claim(金额或 0), key}。"""
    rows = []
    for b in s.get("bets") or []:
        claim = amt_of.get(b["qq"], 0.0)
        rows.append({"qq": b["qq"], "nickname": b["nickname"], "stake": int(b["amount"]),
                     "claim": claim, "key": _pt_key(claim)})
    return rows


def _ranked_groups(rows: list[dict]) -> list[list[dict]]:
    """按排序键（pts/0数/最大位 降序）分名次组；组内保持注序（稳定排序）。

    key 带负号 → 稳定升序即最好名次在前；key=None（无效/0 点）剔除后垫底（各成一组）。"""
    if not rows:
        return []
    ok_rows = [r for r in rows if r["key"] is not None]
    bad_rows = [r for r in rows if r["key"] is None]
    ordered = sorted(ok_rows, key=lambda r: r["key"]) + bad_rows
    groups: list[list[dict]] = []
    cur_key, cur = None, None
    for r in ordered:
        if cur is None or r["key"] != cur_key:
            if cur is not None:
                groups.append(cur)
            cur_key, cur = r["key"], [r]
        else:
            cur.append(r)
    if cur is not None:
        groups.append(cur)
    return groups  # groups[0] = 最高名次


def _settle_dcxx(gid: int, s: dict, amt_of: dict[str, float],
                 rate_permille: int) -> dict:
    """大吃小（无庄）结算，三步（spec §1.3）：

    1) 吃：每名次组（除最末名次）需吃额 = Σ组内注额，从最末名次组本金向上逐个名次吃，
       吃到自己名次为止（不碰同级及以上本金），吃够即停；被吃组内按注序逐个被扣
       （先下注者先被扣满，剩余给后下注者）；下方本金不够则缺口放弃（赢方少得）。
    2) 赢额分配：每名次组（除最末）赢额 = 该组实际吃到的总额（下方本金不足时缺口放弃 =
       只分实吃到部分），组内按注序先拿满（封顶=自己注额）——保证 Σ玩家变动 = -抽水 恒成立。
    3) 抽水：从吃剩的最低位名次组向上收（组内按注序消化）；全吃净仍不足则顺延（极端全同点
       一组则从最高位组依次补收）。delta = 赢额 - 被吃 - 抽水。"""
    rows = _row_amounts(s, amt_of)
    groups = _ranked_groups(rows)
    n = len(rows)
    eaten = [0] * n
    fee_i = [0] * n
    extra = [0] * n
    idx = {id(r): i for i, r in enumerate(rows)}
    pool = sum(r["stake"] for r in rows)
    rate = max(0, min(int(rate_permille or 20), 1000))
    total_fee = pool * rate // 1000

    collected = [0] * len(groups)   # 各名次组实际吃到的总额（缺口放弃时 < 自己注额）
    for gi, g in enumerate(groups[:-1]):
        need = sum(r["stake"] for r in g)
        for lo in range(len(groups) - 1, gi, -1):
            for m in groups[lo]:
                if need <= 0:
                    break
                i = idx[id(m)]
                take = min(need, m["stake"] - eaten[i])
                if take > 0:
                    eaten[i] += take
                    collected[gi] += take
                    need -= take
            if need <= 0:
                break
    # 赢额分配：组内按注序，先下注者先拿满（封顶=自己注额）；只分实吃到的部分
    for gi, g in enumerate(groups[:-1]):
        win = collected[gi]
        for m in g:
            if win <= 0:
                break
            i = idx[id(m)]
            take = min(win, m["stake"])
            extra[i] += take
            win -= take

    for gi in range(len(groups) - 1, -1, -1):
        for m in groups[gi]:
            if total_fee <= 0:
                break
            i = idx[id(m)]
            rem = m["stake"] - eaten[i]
            take = min(total_fee, rem)
            fee_i[i] += take
            total_fee -= take
        if total_fee <= 0:
            break

    events, deltas = [], [0] * n
    for g in groups:
        for m in g:
            i = idx[id(m)]
            deltas[i] = extra[i] - eaten[i] - fee_i[i]
            events.append({"qq": m["qq"], "nickname": m["nickname"],
                           "delta": int(deltas[i])})
    # 汇总表：按点数降序（-点数 升序），同点数按下注顺序（先下注在前）；不含抽水
    ordered = sorted(range(n), key=lambda i: (-_pts_of(rows[i]["key"]), i))
    table = _table_text(
        ["序号", "用户名称", "下注积分", "点数", "结算"],
        [[i + 1, rows[ri]["nickname"], rows[ri]["stake"],
          str(_pts_of(rows[ri]["key"])), f"{deltas[ri]:+d}"]
         for i, ri in enumerate(ordered)],
        right=(0, 2, 3, 4))
    announce = f"本局结果，总分{pool}分：\n{table}"
    return {"events": events, "announce": announce}


def _settle_qzz(gid: int, s: dict, amt_of: dict[str, float],
                rate_permille: int) -> dict:
    """撑庄（有庄）结算：挑战者各自 vs 庄家点数（6 键全序比较），同点组内各自独立对庄家。
    庄家免注但承担抽水（= Σ输家注额 - Σ赢家注额 - 抽水）；庄家无领取记录按 0 点。"""
    boss = s["boss"]
    rows = [dict(b) for b in s.get("bets") or []]
    pool = sum(r["amount"] for r in rows)
    rate = max(0, min(int(rate_permille or 20), 1000))
    fee = pool * rate // 1000
    b_claim = amt_of.get(boss["qq"], 0.0)
    b_key = _pt_key(b_claim)
    events = []
    deltas, c_keys = [], []
    win_sum = lose_sum = 0
    for r in rows:
        c_claim = amt_of.get(r["qq"], 0.0)
        c_key = _pt_key(c_claim)
        c_keys.append(c_key)
        if b_key is None:
            cmp_res = 1 if c_key is not None else 0   # 庄家无效0点：有点的挑战者赢
        elif c_key is None:
            cmp_res = -1
        else:
            cmp_res = (c_key < b_key) - (c_key > b_key)  # key 越小越优（点数大）
        if cmp_res > 0:
            d = r["amount"]
            win_sum += r["amount"]
        elif cmp_res < 0:
            d = -r["amount"]
            lose_sum += r["amount"]
        else:
            d = 0
        deltas.append(d)
        events.append({"qq": r["qq"], "nickname": r["nickname"], "delta": int(d)})
    boss_d = lose_sum - win_sum - fee
    events.append({"qq": boss["qq"], "nickname": boss["nickname"], "delta": int(boss_d)})
    # 汇总表：挑战者按点数降序（-点数 升序），同点数按下注顺序；庄家单列；不含抽水
    ordered = sorted(range(len(rows)), key=lambda i: (-_pts_of(c_keys[i]), i))
    table = _table_text(
        ["序号", "用户名称", "下注积分", "点数", "结算"],
        [[i + 1, rows[ri]["nickname"], rows[ri]["amount"],
          str(_pts_of(c_keys[ri])), f"{deltas[ri]:+d}"]
         for i, ri in enumerate(ordered)],
        right=(0, 2, 3, 4))
    announce = (f"本局结果，总分{pool}分：\n{table}"
                f"\n庄家 {boss['nickname']}（点{_pts_of(b_key)}）：净得 {boss_d:+d}")
    return {"events": events, "announce": announce}


# ---------- 自测（验收向量 = spec §1.3/1.4；python play_rules/rule_fuhe.py） ----------

def _selftest() -> int:
    ok = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok += 1
        if not cond:
            raise SystemExit(f"FAIL: {name} {detail}")

    def new(gid: int, rid: str = "R1") -> None:
        ROUNDS[int(gid)] = _open_state(rid)

    def bet(gid: int, qq: str, nick: str, amt: int) -> str | None:
        return handle_message(gid, int(qq), nick, str(amt))

    # --- 比大小键值逐例（spec §1.2） ---
    check("0.90>0.81（同点比0数）", _cmp_amounts(0.90, 0.81) > 0)
    check("0.27>0.36（同点0数相同比最大位）", _cmp_amounts(0.27, 0.36) > 0)
    check("0.18>0.71（先比点数）", _cmp_amounts(0.18, 0.71) > 0)
    check("0.77 点 4", _pts_text(0.77) == "4")
    check("0.91 点 0", _pts_text(0.91) == "0")
    check("0.01 与 0.10 同键（平）", _cmp_amounts(0.01, 0.10) == 0)
    check("无效金额 1.20 不记点", _pt_key(1.20) is None)
    check("无效金额 0 不记点", _pt_key(0.0) is None)

    # --- 下注/撑庄消息流（spec §1.1） ---
    new(101, "hbjf_1")
    r = bet(101, "11", "甲", 100)
    check("下注回复紧凑累计（1.甲：100）", r == "1.甲：100", str(r))
    check("bettor_qqs 含甲", bettor_qqs(101) == ["11"])
    r = bet(101, "11", "甲", 50)
    check("同人重复下注拒绝", r and "只能下注一次" in r, str(r))
    r = bet(101, "12", "乙", 20)
    check("下注回复追加累计（1.甲：100，2.乙：20）", r == "1.甲：100，2.乙：20", str(r))
    r = handle_message(101, 11, "甲", "撑")
    check("已下注撑庄被拒", r and "不能撑庄" in r, str(r))
    r = handle_message(101, 20, "丙", "撑")
    check("撑庄回复：模式+积分占位", r and "撑庄" in r and "撑庄模式" in r
          and "{balance}" in r, str(r))
    check("mode=qzz", ROUNDS[101]["mode"] == "qzz")
    r = handle_message(101, 21, "丁", "撑")
    check("重复撑庄拒绝", r and "已是本局庄家" in r, str(r))
    bet(101, "22", "戊", 10)
    check("撑庄后继续下注 ok", len(ROUNDS[101]["bets"]) == 3)
    check("撑庄需开奖 4 人", claim_need(101) == 4)
    seal = handle_seal(101, 20)
    check("封盘 ok 且带 banker_check", seal["ok"] and seal["banker_check"] is not None)
    check("banker need = pool+fee = 130 + 2", seal["banker_check"]["need"] == 132,
          str(seal["banker_check"]))
    check("封盘文案：合计+3列表格", seal["text"].startswith("停止下注，合计130分：")
          and "用户名称" in seal["text"] and "下注积分" in seal["text"],
          str(seal["text"])[:150])
    r = bet(101, "13", "己", 5)
    check("封盘后下注被拦", r and "封盘" in r, str(r))
    r = handle_redpacket(101, 22, "戊", 0.77)
    check("正常领取静默（空串回执不 @，delta 0）", r == ("", 0), str(r))
    r = handle_redpacket(101, 22, "戊", 1.20)
    check(">0.99 提示不计点", r and "异常" in r[0] and r[1] == 0, str(r))
    r = handle_redpacket(101, 30, "路人", 0.30)
    check("非参与人静默", r is None or r[0] is None)
    snap = bet_snapshot(101)
    check("bet_snapshot 尾行是庄家", snap and snap[-1].get("boss")
          and snap[-1]["qq"] == "20", str(snap))
    check("bettor_qqs 不含庄家", "20" not in bettor_qqs(101))
    _reset(101)

    # --- 撑庄结算验收向量（spec §1.4：甲撑庄 乙丙丁戊押 20/80/300/100 → need 510，庄 -10） ---
    new(102, "hbjf_2")
    boss = handle_message(102, 40, "甲", "撑")
    check("甲撑庄", boss and "甲" in boss, str(boss))
    for qq, nick, amt in (("41", "乙", 20), ("42", "丙", 80),
                          ("43", "丁", 300), ("44", "戊", 100)):
        bet(102, qq, nick, amt)
    seal = handle_seal(102, 20)
    check("qzz 封盘 need=510", seal["banker_check"]["need"] == 510, str(seal))
    # 乙(0.60→6点) > 丙(0.50→5点) > 甲(庄,0.40→4点) = 丁(0.40→4点) > 戊(0.20→2点)
    claims = [{"qq": "41", "amount": 0.60},   # 乙 6 点
              {"qq": "42", "amount": 0.50},   # 丙 5 点
              {"qq": "40", "amount": 0.40},   # 甲(庄) 4 点
              {"qq": "43", "amount": 0.40},   # 丁 4 点（与庄同键 → 平）
              {"qq": "44", "amount": 0.20}]   # 戊 2 点
    res = settle_redpacket(102, claims, 20)
    check("qzz 结算返回", res is not None)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    check("乙 +20", deltas.get("41") == 20, str(deltas))
    check("丙 +80", deltas.get("42") == 80, str(deltas))
    check("丁 平 0", deltas.get("43") == 0, str(deltas))
    check("戊 -100", deltas.get("44") == -100, str(deltas))
    check("庄家 -10", deltas.get("40") == -10, str(deltas))
    check("announce：总分表+庄家行，无抽水", "本局结果，总分500分" in res["announce"]
          and "庄家" in res["announce"] and "净得" in res["announce"]
          and "抽水" not in res["announce"], res["announce"])
    check("Σ玩家=-fee", sum(deltas.values()) == -10, str(deltas))
    _reset(102)

    # --- 大吃小验收向量（spec §1.3：100/20/80/200 A>B>C>D -> +100/+20/+72/-200 fee8） ---
    new(103, "hbjf_3")
    for qq, nick, amt in (("51", "A", 100), ("52", "B", 20),
                          ("53", "C", 80), ("54", "D", 200)):
        bet(103, qq, nick, amt)
    handle_seal(103, 20)
    # A(0.90→9点,含0优先) > B(0.81→9点,无0) > C(0.50→5点) > D(0.20→2点)
    claims = [{"qq": "51", "amount": 0.90},   # A 9 点（0.90: 9+0=9, 0数1）
              {"qq": "52", "amount": 0.81},   # B 9 点 0数0 大位8 < A → A>B
              {"qq": "53", "amount": 0.50},   # C 5 点
              {"qq": "54", "amount": 0.20}]   # D 2 点
    res = settle_redpacket(103, claims, 20)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    check("A +100", deltas.get("51") == 100, str(deltas))
    check("B +20", deltas.get("52") == 20, str(deltas))
    check("C +72（吃80 抽水8）", deltas.get("53") == 72, str(deltas))
    check("D -200（被吃满）", deltas.get("54") == -200, str(deltas))
    check("Σ玩家=-fee", sum(deltas.values()) == -8, str(deltas))
    body = res["announce"].splitlines()
    pos = {n: next(i for i, l in enumerate(body) if n in l) for n in ("A", "B", "C", "D")}
    check("结果表按点数降序（同点按注序 A 先）", pos["A"] < pos["B"] < pos["C"] < pos["D"],
          str(pos))
    check("dcxx announce 总分无抽水", res["announce"].startswith("本局结果，总分400分")
          and "抽水" not in res["announce"], res["announce"][:80])
    _reset(103)

    # --- 同点组内按注序消化（spec 向量：X200/Y100 同点最低，上名次吃250+抽水30） ---
    new(104, "hbjf_4")
    bet(104, "61", "X", 200)
    bet(104, "62", "Y", 100)
    bet(104, "63", "Z", 250)
    handle_seal(104, 55)   # 550*55‰ = 30
    claims = [{"qq": "61", "amount": 0.30},   # X/Y 同键：(3+0)%10=3 点
              {"qq": "62", "amount": 0.30},
              {"qq": "63", "amount": 0.50}]   # Z：(5+0)%10=5 点 > 3
    res = settle_redpacket(104, claims, 55)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    check("X 先被扣满 200", deltas.get("61") == -200, str(deltas))
    check("Y 再扣 80（组内剩 20 归 Y）", deltas.get("62") == -80, str(deltas))
    check("Z 赢 +250", deltas.get("63") == 250, str(deltas))
    check("Σ=-30 fee", sum(deltas.values()) == -30, str(deltas))
    _reset(104)

    # --- seal/void/claim_need 边界 ---
    new(105, "hbjf_5")
    r = handle_seal(105, 20)
    check("无人下注 seal 作废", r["ok"] is False and "无人下注" in r["text"], str(r))
    check("作废已复位", claim_need(105) == 0)
    new(105, "hbjf_6")
    bet(105, "70", "A", 10)
    r = handle_void(105)
    check("handle_void 返回公告并复位", r and "作废" in r and claim_need(105) == 0, str(r))
    r = handle_void(105)
    check("无局 void 返回 None", r is None)
    new(105, "hbjf_7")
    bet(105, "70", "A", 10)
    info = seal_info(105)
    check("seal_info betting", info["phase"] == "betting" and info["claim_need"] == 1)
    check("seal_info 无庄 mode dcxx", info["mode"] == "dcxx")
    _reset(105)

    # --- 缺口放弃（吃不满不回补）+ 抽水顺延（A/B 同点最高，C 最末） ---
    new(106, "hbjf_8")
    bet(106, "80", "A", 100)
    bet(106, "81", "B", 100)
    bet(106, "82", "C", 100)
    handle_seal(106, 100)   # pool300 fee30
    claims = [{"qq": "80", "amount": 0.60}, {"qq": "81", "amount": 0.60},
              {"qq": "82", "amount": 0.10}]
    res = settle_redpacket(106, claims, 100)
    deltas = {e["qq"]: e["delta"] for e in res["events"]}
    # 最高名次组 [A,B] 需吃 200，C(100) 吃净后缺口 100 放弃 → 该组实得只有 100；
    # 赢额按注序只分实吃到部分：A 先拿满 100 → B 分文未得（平）；抽水 30 从最末组向上：
    # C 已吃净(rem 0) → [A,B] 组内按注序 A 扣 30 → A = 100-30 = +70；B = 0
    check("A +70（分得 C 的 100，fee30 顺延 A 扣）", deltas.get("80") == 70, str(deltas))
    check("B 平 0（缺口放弃只分实吃到的，B 后注分文未得）", deltas.get("81") == 0, str(deltas))
    check("C -100（被吃满）", deltas.get("82") == -100, str(deltas))
    check("Σ=-30（恒守恒）", sum(deltas.values()) == -30, str(deltas))
    _reset(106)

    # 开局：群内文字「开始游戏」只引导去面板（防规则状态与 agent 会话脱钩）；handle_round_start
    # （GUI「开始本局」按钮调用）才真正开局
    r = handle_message(107, 90, "新", "开始游戏")
    check("群内文字开局被引导去面板", r and "开始本局" in r, str(r))
    check("未被文字开局", betting_open(107) is False)
    r = handle_round_start(107, "hbjf_9")
    check("round_start 开场白含局号", r and "hbjf_9" in r, str(r))
    check("开局后 betting", betting_open(107) is True)
    r = handle_message(107, 91, "新", "开始游戏")
    check("局中文字开始被拒（提示先结算/作废）", r and "进行中" in r, str(r))
    r = handle_round_end(107)
    check("handle_round_end 复位", betting_open(107) is False)
    _reset(107)
    check("reset 后 idle", betting_open(107) is False and claim_need(107) == 0)

    print(f"rule_fuhe selftest OK ({ok} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
