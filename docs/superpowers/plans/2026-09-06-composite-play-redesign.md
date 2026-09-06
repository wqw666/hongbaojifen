# 复合玩法（大吃小×抢庄）唯一玩法重做 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除全部 6 个旧玩法，只保留一个全新「复合玩法」（大吃小×抢庄合体），并把 agent 游戏玩法页改成该唯一玩法的操作台（停止下注/可编辑开奖表格/手动结算），配套收敛总后台与文档版本。

**Architecture:** 玩法状态按群键控的纯 Python 规则文件 `rule_fuhe.py`（热重载协议 v2，另新增 handle_seal/handle_void/claim_need/bet_snapshot/seal_info 可选协议）；play_engine 增加通用 `call_rule` 转发；redpacket_game 从「领完自动分阶段收尾」改为「手动结算驱动」并实现首红包不足作废；play_tab GUI 改为群切换器 + 四动作按钮 + 右侧半宽可编辑开奖表格；总后台种子/存储收敛只留复合玩法。

**Tech Stack:** Python 3 + CustomTkinter（agent GUI）、importlib 热加载玩法文件、Java Spring Boot + MySQL 8 + Flyway（总后台，本次无迁移）、HTTP 回调 + NapCat 插件（消息通道，不改插件）。

**Spec:** `docs/superpowers/specs/2026-09-06-composite-play-redesign.md`（本计划从 spec 论证；执行者须先读 spec 再读本计划，二者一起构成完整契约）。

## Global Constraints

- **不主动 git commit**（仓库约定：提交由用户在评审后统一发起；各任务末尾无 commit 步骤）
- agent 更新必须 bump `agent/gui/app/main_window.py` L29 `APP_VERSION`（当前 `2026.09.06-11` → 本计划改 `2026.09.06-12`，仅任务 6 做）并同步 `agent/docs/代码架构说明.md` 行 4 版本引用与 §8 玩法列表
- 玩法文件双仓库同步：`agent/play_rules/rule_fuhe.py` 与总后台存储文件内容一致（md5 校验）；总后台文件名先 `SELECT file_name FROM play_rule_files WHERE id=?` 确认真实值
- 8892 重启纪律（任务 5 用）：netstat 找 8892 监听 PID → 父进程（mvn）→ `taskkill /PID <mvn> /T /F` → 同参数 Start-Process 后台重启（stdout 重定向日志）→ 轮询 `/health`
- 新增 Flyway 迁移须唯一递增版本——**本次不需要任何迁移**（数据库行变更走 SQL/服务代码，不走迁移文件）
- Windows Git Bash 环境；agent Python 命令在 `D:\work\hongbaojifen\agent` 下执行；GUI 无 headless 测试，关口 = `py_compile` + 代码评审 + 任务 6 手工全链路
- 玩法/配置文本沿用仓库中文文案风格；所有群号/QQ 在代码内一律 str，仅在跨层转换时 int

---

### Task 1: 新建唯一玩法文件 `agent/play_rules/rule_fuhe.py`（含内建 selftest 全向量）

**Files:**
- Create: `agent/play_rules/rule_fuhe.py`（完整内容见下方步骤 1 代码块，直接整文件写入）
- 参考（不修改）：`agent/play_rules/rule_dcxx.py`（模块 docstring/自测风格参照）

**Interfaces:**
- Consumes: 引擎协议 v2 文档（`agent/integration/play_engine.py` L1-33 docstring）；本文件为纯标准库，无 import 依赖
- Produces（供任务 2-4 使用，签名必须与下面代码一致，不得改动）:
  - `handle_message(group_id:int, qq:int, nickname:str, text:str) -> str|None`（delta 恒 0）
  - `handle_round_start(group_id, round_id="") -> str|None`（返回开场白文本）
  - `handle_round_end(group_id) -> None`、`handle_round_abort(group_id) -> str|None`
  - `handle_redpacket(group_id, qq, nickname, amount) -> tuple[str|None, int]`（delta 恒 0）
  - `settle_redpacket(group_id, claims:list[dict], rate_permille=20) -> dict|None`（返回 `{"events":[{"qq","nickname","reply","delta"}],"announce":str}`）
  - `bettor_qqs(group_id) -> list[str]`、`betting_open(group_id) -> bool`
  - `handle_seal(group_id, rate_permille=20) -> dict`（`{ok:bool, text:str, banker_check:dict|None}`；banker_check 仅抢庄）
  - `handle_void(group_id) -> str|None`（返回作废公告并复位该群）
  - `claim_need(group_id) -> int`（有庄=下注人数+1，无庄=下注人数；非 betting/sealed 返回 0）
  - `bet_snapshot(group_id) -> list[dict]`（注序 rows `{qq,nickname,amount}`，有庄末尾追加 `{qq,nickname,amount:0,boss:True}`）
  - `seal_info(group_id) -> dict`（`{phase,mode,round_id,boss,n_bets,pool,fee,claim_need}`；无局 `{phase:"idle",...}`）
  - 模块级 `ROUNDS: dict[int, dict]`（群状态；文件热重载会清空，docstring 注明「更新玩法文件请先无进行中局」）

- [ ] **Step 1: 整文件写入 `agent/play_rules/rule_fuhe.py`**（内容如下，逐字落盘）

```python
"""复合玩法（大吃小×抢庄，唯一玩法，协议 v2）。

玩法：默认大吃小；下注期有人发「撑」→ 此人成庄家（先到先得，已下注者不能抢），
本局转抢庄模式。封盘后管理员发红包（每份 0.01~0.99 元，份数 >= 需开奖人数，
抢庄 = 下注人数 + 庄家 1 份），红包金额定大小；所有人领完由 agent 手动点「结算」，
本文件 settle_redpacket 按大吃小/抢庄算法结算。

积分只经红包结算产生：handle_message/handle_redpacket 均返回 delta 0。

注意：本文件会被引擎热重载，import 即清空 ROUNDS —— 更新玩法文件前请先结束进行中的局。

比大小（两种模式共用，金额取 f"{amount:.2f}" 两位 cents 数字 (a,b)）：
  点数 pts = (a + b) % 10（0.77 -> 7+7=14 -> 4；0.91 -> 9+1=10 -> 0）
  排序键 key = (-pts, -zeros, -hi)，升序排即 点数大优先 -> 含0多优先 -> 最大位大优先；
  三键全同 = 同名次组，组内按下注顺序逐个消化（先下注先拿满/先被扣满，封顶=自己注额）。
  金额不在 (0, 0.99] 视为无效：点数 0、永远最末名次、不产生盈亏（agent 表格会标红提示修正）。

自测：python play_rules/rule_fuhe.py，须全绿。
"""
from __future__ import annotations

import time

ROUNDS: dict[int, dict] = {}  # group_id -> 局状态

_CMD_START = {"开始游戏", "开局"}
_OPENING = ("游戏开始！本局对局编号：{round_id}。玩法：复合玩法（大吃小×抢庄）："
            "直接发数字下注（如 500，整数积分）；想当庄家的发「撑」抢占（先到先得，本局转抢庄）。"
            "下注结束后管理员发红包（每份 0.01~0.99 元，份数不少于需开奖人数），红包金额定大小。")
_SEAL_HEAD = "停止下注！本局对局编号：{round_id}，共 {n} 人下注，合计 {pool} 积分："


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


def _bet_list_text(s: dict) -> str:
    return "\n".join(f"{i}. {b['nickname']} 下注 {b['amount']}"
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


def _cmp_amounts(a: float, b: float) -> int:
    """a 对 b 比较：>0 大、<0 小、0 平（前 3 键全同=平；平局消化见 settle）。"""
    ka, kb = _pt_key(a), _pt_key(b)
    if ka is None and kb is None:
        return 0
    if ka is None:
        return -1
    if kb is None:
        return 1
    return (ka[0] > kb[0]) - (ka[0] < kb[0]) or (ka[1] > kb[1]) - (ka[1] < kb[1]) \
        or (ka[2] > kb[2]) - (ka[2] < kb[2])


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
            return f"本局已封盘（{s.get('round_id') or ''}），等待开奖，请勿再下注/抢庄。"
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
    boss_tail = (f" ｜ 庄家 {s['boss']['nickname']}，其余人继续发数字押注挑战"
                 if s.get("boss") else "")
    return (f"本局 {s.get('round_id') or '（本局）'}，已收到 {nick or '您'} 下注 {int(m)} 积分，"
            f"当前累计名单：\n{_bet_list_text(s)}{boss_tail}")


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
        return f"{s['boss']['nickname']} 已是本局庄家，不能重复抢庄"
    if any(b["qq"] == qq_s for b in s["bets"]):
        return "已下注不能抢庄（先下注的按大吃小参与），请等待下一局"
    s["boss"] = {"qq": qq_s, "nickname": nick or f"QQ{qq_s}"}
    s["mode"] = "qzz"
    return (f"🎤 {s['boss']['nickname']} 抢到庄家！本局转抢庄模式（下注的挑战庄家点数），"
            f"其余人发数字押注挑战，庄家坐庄不用下注。")


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

    返回 {ok, text, banker_check}；抢庄时 banker_check={qq,nickname,need}（GUI 查余额预检）。
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
    text = (_SEAL_HEAD.format(round_id=s.get("round_id") or "", n=len(bets), pool=pool)
            + "\n" + _bet_list_text(s)
            + f"\n管理员请发红包开奖{need_tail}（每份 0.01~0.99 元，份数不少于 {need} 份）。")
    s["sealed_msg"] = text
    bc = None
    if s.get("boss"):
        bc = {"qq": s["boss"]["qq"], "nickname": s["boss"]["nickname"],
              "need": pool + fee}
    return {"ok": True, "text": text, "banker_check": bc}


# ---------- 红包领取（agent 编排层逐包回调；本文件不存金额，结算时再取） ----------

def handle_redpacket(group_id: int, qq: int, nickname: str, amount: float):
    """红包领取事件：只回执提示（delta 0）。未参与本局静默；金额无效(>0.99)提示修正。

    返回 (reply|None, 0)。真正记点数/盈亏在 settle_redpacket 用最终 claims 结算。"""
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
    return f"已记录：抢到 {amt:.2f} 元（点数 {_pts_text(amt)}），等待结算", 0


# ---------- 结算（settle_redpacket：大吃小 / 抢庄） ----------

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

    lines, events = [], []
    for rank_i, g in enumerate(groups, 1):
        for m in g:
            i = idx[id(m)]
            d = extra[i] - eaten[i] - fee_i[i]
            mark = "赢" if d > 0 else ("亏" if d < 0 else "平")
            pts_txt = _pts_text(m["claim"]) if m["key"] is not None else "0"
            lines.append(f"{rank_i}. {m['nickname']} 下注{m['stake']} 抢到"
                         f"{m['claim']:.2f} 点{pts_txt}：{d:+d}")
            events.append({"qq": m["qq"], "nickname": m["nickname"],
                           "reply": f"本局开奖：您{m['stake']}注额结算 {d:+d}（{mark}）",
                           "delta": int(d)})
    announce = (f"本局开奖（大吃小）！总下注 {pool} 积分，抽水 {pool * rate // 1000}：\n"
                + "\n".join(lines))
    return {"events": events, "announce": announce}


def _settle_qzz(gid: int, s: dict, amt_of: dict[str, float],
                rate_permille: int) -> dict:
    """抢庄（有庄）结算：挑战者各自 vs 庄家点数（6 键全序比较），同点组内各自独立对庄家。
    庄家免注但承担抽水（= Σ输家注额 - Σ赢家注额 - 抽水）；庄家无领取记录按 0 点。"""
    boss = s["boss"]
    rows = [dict(b) for b in s.get("bets") or []]
    pool = sum(r["amount"] for r in rows)
    rate = max(0, min(int(rate_permille or 20), 1000))
    fee = pool * rate // 1000
    b_claim = amt_of.get(boss["qq"], 0.0)
    b_key = _pt_key(b_claim)
    b_pts = "0" if b_key is None else str(-b_key[0])
    lines, events = [], []
    win_sum = lose_sum = 0
    for i, r in enumerate(rows, 1):
        c_claim = amt_of.get(r["qq"], 0.0)
        c_key = _pt_key(c_claim)
        if b_key is None:
            cmp_res = 1 if c_key is not None else 0   # 庄家无效0点：有点的挑战者赢
        elif c_key is None:
            cmp_res = -1
        else:
            cmp_res = (c_key < b_key) - (c_key > b_key)  # key 越小越优（点数大）
        if cmp_res > 0:
            d, mark = r["amount"], "赢"
            win_sum += r["amount"]
        elif cmp_res < 0:
            d, mark = -r["amount"], "亏"
            lose_sum += r["amount"]
        else:
            d, mark = 0, "平"
        pts_txt = _pts_text(c_claim) if c_key is not None else "0"
        lines.append(f"{i}. {r['nickname']} 押{r['amount']} 点{pts_txt}：{d:+d}")
        events.append({"qq": r["qq"], "nickname": r["nickname"],
                       "reply": f"本局抢庄开奖：押{r['amount']} vs 庄家点{b_pts} → {mark} {d:+d}",
                       "delta": int(d)})
    boss_d = lose_sum - win_sum - fee
    lines.append(f"庄家 {boss['nickname']}（点{b_pts}，免注承担抽水 {fee}）：净得 {boss_d:+d}")
    events.append({"qq": boss["qq"], "nickname": boss["nickname"],
                   "reply": f"本局抢庄：庄家净得 {boss_d:+d}（抽水 {fee}）", "delta": int(boss_d)})
    announce = (f"本局开奖（抢庄）！庄家 {boss['nickname']} 点数 {b_pts}，挑战押注总额 {pool}，"
                f"抽水 {fee}：\n" + "\n".join(lines))
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

    # --- 下注/抢庄消息流（spec §1.1） ---
    new(101, "hbjf_1")
    r = bet(101, "11", "甲", 100)
    check("甲下注登记", r and "甲" in r and "100" in r, str(r))
    check("bettor_qqs 含甲", bettor_qqs(101) == ["11"])
    r = bet(101, "11", "甲", 50)
    check("同人重复下注拒绝", r and "只能下注一次" in r, str(r))
    bet(101, "12", "乙", 20)
    r = handle_message(101, 11, "甲", "撑")
    check("已下注抢庄被拒", r and "不能抢庄" in r, str(r))
    r = handle_message(101, 20, "丙", "撑")
    check("丙抢到庄", r and "抢到庄家" in r, str(r))
    check("mode=qzz", ROUNDS[101]["mode"] == "qzz")
    r = handle_message(101, 21, "丁", "撑")
    check("重复抢庄拒绝", r and "已是本局庄家" in r, str(r))
    bet(101, "22", "戊", 10)
    check("抢庄后继续下注 ok", len(ROUNDS[101]["bets"]) == 3)
    check("抢庄需开奖 4 人", claim_need(101) == 4)
    seal = handle_seal(101, 20)
    check("封盘 ok 且带 banker_check", seal["ok"] and seal["banker_check"] is not None)
    check("banker need = pool+fee = 130 + 2", seal["banker_check"]["need"] == 132,
          str(seal["banker_check"]))
    r = bet(101, "13", "己", 5)
    check("封盘后下注被拦", r and "封盘" in r, str(r))
    r = handle_redpacket(101, 22, "戊", 0.77)
    check("抢红包回执有点数且 delta 0", r and "点" in r[0] and r[1] == 0, str(r))
    r = handle_redpacket(101, 22, "戊", 1.20)
    check(">0.99 提示不计点", r and "异常" in r[0] and r[1] == 0, str(r))
    r = handle_redpacket(101, 30, "路人", 0.30)
    check("非参与人静默", r is None or r[0] is None)
    snap = bet_snapshot(101)
    check("bet_snapshot 尾行是庄家", snap and snap[-1].get("boss")
          and snap[-1]["qq"] == "20", str(snap))
    check("bettor_qqs 不含庄家", "20" not in bettor_qqs(101))
    _reset(101)

    # --- 抢庄结算验收向量（spec §1.4：甲撑庄 乙丙丁戊押 20/80/300/100 → need 510，庄 -10） ---
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
    check("announce 含庄家净得", "庄家" in res["announce"] and "净得" in res["announce"])
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
```

（Step 1 完成后如 selftest 对不上某条向量，先对照 spec §1.3 算法步骤 4-7 修正**算法**，不要改向量。）

- [ ] **Step 2: 运行自测，全部通过**

Run（在 `D:\work\hongbaojifen\agent` 目录）: `python play_rules/rule_fuhe.py`
Expected: `rule_fuhe selftest OK (N checks)`，无 FAIL。

- [ ] **Step 3: 交叉验算**：文件内已含全部 spec 验收向量（V1 大吃小 Σ=-fee、V2 同点组按注序消化 X-200/Y-80/Z+250、V3 抢庄 庄-10 need510）。任何一条不符 → 停下修正，不带病进入下一任务。

---

### Task 2: play_engine 增加通用转发 `call_rule` + 协议文档

**Files:**
- Modify: `agent/integration/play_engine.py`（docstring 协议表、import 行、新方法插在 `betting_open` 之后、`_notify_optional` 之前）

**Interfaces:**
- Consumes: 任务 1 的新协议函数（handle_seal/handle_void/claim_need/bet_snapshot/seal_info/handle_round_start 返回文本）
- Produces: `RuleEngine.call_rule(fn_name: str, group_id: int|str, *args) -> Any`（供任务 3/4 使用；未激活/无函数/异常 → None 并记 last_error）

- [ ] **Step 1: docstring 协议表补三行**（在现有可选协议列表后追加）

```python
    def handle_seal(group_id, rate_permille=20)                          # 「停止下注」：封盘并返回 {ok,text,banker_check}|None
    def handle_void(group_id)                                            # 作废：返回作废公告文本并复位该群状态
    def claim_need(group_id)                                             # 本局需开奖人数（红包份数不足判定；非下注/封盘期=0）
    def bet_snapshot(group_id)                                           # 注序下注明细（供 GUI 开奖表格）
    def seal_info(group_id)                                              # 当前局状态快照（供 GUI 状态栏/按钮门控）
```
并在 docstring 说明：`handle_round_start` 可返回开场白文本（GUI 播报用）；带返回值的可选协议
一律经 `engine.call_rule(fn_name, group_id, *args)` 调用（引擎不解析返回值协议类型，原样返回）。

- [ ] **Step 2a: 补 import**：在 `from pathlib import Path`（L43）之后加一行 `from typing import Any`（文件当前无 typing 导入）

- [ ] **Step 2b: 新增 call_rule 方法**（插在 `betting_open` 方法后、`_notify_optional` 前）

```python
    def call_rule(self, fn_name: str, group_id: int | str, *args) -> Any:
        """通用转发：调当前激活玩法的 fn_name(group_id, *args)，原样返回其返回值。

        供 handle_seal/handle_void/claim_need/bet_snapshot/seal_info 及所有"要返回值"
        的新协议使用（热重载语义同其它入口）；未激活/玩法未定义该函数/调用异常 → None
        并记 last_error。注意：不经过同 qq 去重（供 GUI 按钮调用，非群消息路径）。"""
        module = self._current_module()
        if module is None:
            return None
        fn = getattr(module, fn_name, None)
        if not callable(fn):
            return None
        try:
            return fn(int(group_id), *args)
        except Exception as e:  # noqa: BLE001 — 玩法 bug 不影响引擎
            self.last_error = f"玩法{fn_name}异常: {e.__class__.__name__}: {e}"
            return None
```

- [ ] **Step 3: 跑引擎自测确认无回归**

Run: `python -m integration.play_engine`
Expected: `selftest OK`（结尾行样式同现有；无 FAIL/异常）。

---

### Task 3: redpacket_game 手动结算驱动改造 + 首红包不足作废

**Files:**
- Modify: `agent/integration/redpacket_game.py`（announced 分支、删分阶段收尾、新增手动结算三方法、_apply_batch_settle 改签名、retry_pending 收尾补清局、_selftest 重写）
- `rounds`（传统单红包模式，L170-226）、`end_round`、`cancel_rounds`、`record_chat`、`_announce_round_summary` **保留不动**（复合玩法不再经过这些路径，但 legacy 语义必须不回归）

**Interfaces:**
- Consumes: Task 2 `engine.call_rule` 的能力（经 play_tab 注入 `claim_need` 回调）；Task 1 的 claim_need/bettor_qqs 语义
- Produces（供 Task 4 GUI 使用，签名锁定）:
  - `RedPacketGame.__init__(..., claim_need: Callable[[str], int] | None = None)`（新可选注入追加到参数末尾）
  - ann（announced[gid]）新增字段：`sealed: bool`、`first_bill: dict|None`（`{bill,total_num,recv_num}`）、`ready_to_settle: bool`、`rate_permille: int`、`batch_applied: bool`、`settle_results: list`
  - `seal_round(group_id) -> None`（置 sealed + A3 判定：首红包已知不足 → 立即作废）
  - `settle_round_now(group_id) -> dict` → `{ok:True, results:[{qq,nickname,delta}]}` 或 `{ok:False, error}`
  - `set_claim_amount(group_id, qq, amount, nickname="") -> str|None`
  - `void_round(group_id, announce_text=None) -> None`
  - 删除：模块常量 `STAGE_SETTLE_SEC`/`STAGE_DETAIL_SEC`、`_claim_end_reason`、`_schedule_round_end`、`_batch_settle_once`；`poll_auto_end` 改为空壳（worker 每 0.5s 调用点保留）
  - 新增私有：`_need_claimers(gid) -> int`、`_evaluate_round(gid, ann, payload=None) -> None`

**行为改动步骤（先通读对应现状代码再逐块改）：**

- [ ] **Step 1: docstring（L1-22）改写**：删「领完 → +10s/+10s 分阶段自动收尾」描述，改述为：本局模式 = 手动结算驱动 —— 红包事件记入当前局，只认第 1 个管理员红包（D6）；封盘后红包份数 < 需开奖人数 → 立即作废（A3）；「结算」由 GUI 按钮调 `settle_round_now`（批量结算 → 上报成功才播报并清局）；「作废本局」调 `void_round`。STAGE 常量说明句一并删。

- [ ] **Step 2: __init__ 参数与字段**：参数末尾追加 `claim_need: Callable[[str], int] | None = None`，函数体存 `self._claim_need = claim_need`。（`Callable` 已在文件顶部导入则直接用，否则在顶部 from typing 补。）

- [ ] **Step 3: start_round 的 ann 初始字典**追加字段（L343-346 处）：

```python
        self.announced[gid] = {
            "group_id": gid, "round_id": str(round_id), "play_name": play_name,
            "events": [], "claims": {}, "ended": False,
            "sealed": False, "first_bill": None, "ready_to_settle": False,
            "rate_permille": 20, "batch_applied": False, "settle_results": [],
        }
```

- [ ] **Step 4: handle_claim 的 announced 分支（L124-168）重写**（admin 校验与 closed_bills 护栏 L92-121 不动；传统 rounds 尾部 L170+ 不动）：

```python
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
                return  # 不接受第 2 个管理员红包
            else:
                fb["total_num"] = max(int(fb.get("total_num") or 0), total_num)
                fb["recv_num"] = max(int(fb.get("recv_num") or 0), recv_num)
            ann.setdefault("bills", set()).add(bill)
            if int(payload.get("rate_permille") or 0) > 0:
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
                        "reply": str(reply_text or f"获得{int(delta or 0)}积分"),
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
```

- [ ] **Step 5: 删除分阶段收尾三方法 + STAGE 常量 + poll_auto_end 空壳**

删除 `_claim_end_reason`（L251-264）、`_schedule_round_end`（L266-284）、`_batch_settle_once`（L286-291）整块方法，及 L32-33 的 `STAGE_SETTLE_SEC`/`STAGE_DETAIL_SEC` 常量。`end_round`（L365-419）中 L394-395 的 `if ann.get("claim_done") and not ann.get("batch_done"): self._batch_settle_once(...)` 两行删除（claim_done 不再置位，永不触发；防引用已删方法）。`poll_auto_end`（L489-508）整体替换为空壳：

```python
    def poll_auto_end(self) -> None:
        """worker 每 0.5s 调用点：分阶段自动收尾已废弃（改为 GUI「结算」按钮手动驱动）。
        保留空壳兼容调用方；无自动行为。"""
        return
```

- [ ] **Step 6: 新增私有判定 + 三个新方法**（插在 `end_round` 之后、`_announce_round_summary` 之前；`_query_abort_text`/`_current_bettors` 保留）

```python
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
        else:
            ann["claims"][qq_s] = {"qq": qq_s, "name": nickname or f"用户{qq_s}",
                                   "amount": round(amt, 2), "delta": 0,
                                   "reply": "", "edited": True}
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
```

- [ ] **Step 7: `_apply_batch_settle` 改 claims-list 签名**（L444-477 整块替换；rate 从 ann 取——封盘时/每次红包事件已随 payload 写入 ann["rate_permille"]）：

```python
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
```

- [ ] **Step 8: retry_pending 的 announced 分支补清局**（成功块现为 L564-567：`del self.pending_announced[rid]` → `self.last[rid]=ok` → `_announce_round_summary(ann)` → `_log(...)`）：在 `self._announce_round_summary(ann)`（L566）之后、日志行（L567）之前追加两行，防与 GUI 本局残留重复结算：

```python
            self.announced.pop(str(ann.get("group_id") or ""), None)  # 防同局残留重复结算
            self._notify_round_end(str(ann.get("group_id") or ""))
```
（同分支现有 `self._log(...)` 行保留在最后。）

- [ ] **Step 9: `_selftest` 整块重写**（L573-767），覆盖：手动结算成功路径 + 编辑改值/补值参与结算、首次上报失败→pending→重试成功清局、重复结算拒绝、封盘后首红包不足即作废、只认第 1 个红包（第 2 单号忽略）、同单号幂等、领完 ready、legacy 传统单红包模式不回归、非管理员/机器人自己跳过：

```python
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
                         send_announce=lambda g, t: announces.append((g, t)),
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
```

（若个别断言与实现细节有出入，以「结算成功后才播报/清局」「只认第 1 个红包」「份数不足即作废」三条产品行为为唯一准绳微调测试，**不要**为迁就测试放宽产品行为。文件底部 `if __name__ == "__main__":` 保留。）

- [ ] **Step 10: 运行自测**：`python -m integration.redpacket_game` → Expected: `selftest OK (N checks)`

---

### Task 4: play_tab GUI 重构（操作台 + 群切换器 + 右侧可编辑开奖表格）

**Files:**
- Modify: `agent/gui/app/play_tab.py`（改动大：docstring、__init__ 状态、_build_ui 布局、按钮/处理器增删、表格卡、_auto_restore 自动激活）
- Modify: `agent/gui/app/config_manager.py`（AppConfig 显式字段类——加一个字段）

**Interfaces:**
- Consumes: Task 1 规则函数（engine.call_rule 访问）、Task 3 的 rp 方法（seal_round/settle_round_now/set_claim_amount/void_round/announced 字段）
- Produces: 页面自身（无对外新接口；cfg 持久化字段 `play_group_current`）

- [ ] **Step 1: config_manager.py 加字段**（AppConfig 类内，`play_group_id` 字段附近）：

```python
    play_group_current: str = ""                 # 玩法页当前操作群（按钮/表格作用对象）
```

- [ ] **Step 2: docstring 更新**：页面顶部能力描述改为复合玩法操作台；注明：多群各开各局，按钮/表格只作用于「当前群」（群切换器）。

- [ ] **Step 3: __init__ 状态字段**（L86-90 附近追加）：

```python
        self._cur_group: str = ""                # 当前操作群
        self._table_rows: dict[str, list] = {}   # 群 -> 表格行缓存（结算后保留展示用）
        self._table_live: set[str] = set()       # 群 -> 有活动本局（live 数据源有效）
```

- [ ] **Step 4: 删除玩法选择卡与旧按钮，重建按钮行**（`_build_ui` 中）

删除：玩法选择整卡（L111-123 及其 opt_rule/lbl_rules/刷新玩法列表 相关）、`_refresh_rules`/`_apply_rules_ui`/`_on_rule_picked`/`_selected_rule`（L826-873）整组方法；旧按钮行 L162-175 的「启用玩法/停止玩法/连续开局/开始本局/结束本局/结算并上报/重试未上报」按各自新职责重建。新按钮行：

```python
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        ctk.CTkLabel(row, text="当前群", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(0, 6))
        self.opt_group = ctk.CTkOptionMenu(row, values=["（无游戏群）"], width=160,
                                           command=self._on_group_picked)
        self.opt_group.pack(side="left", padx=(0, 12))
        ctk.CTkButton(row, text="开始本局", width=90, command=self._start_round_cur).pack(side="left", padx=4)
        self.btn_seal = ctk.CTkButton(row, text="停止下注", width=90, command=self._seal_round_cur)
        self.btn_seal.pack(side="left", padx=4)
        self.btn_settle = ctk.CTkButton(row, text="结算", width=80, command=self._settle_round_cur)
        self.btn_settle.pack(side="left", padx=4)
        self.btn_void = ctk.CTkButton(row, text="作废本局", width=90, command=self._void_round_cur)
        self.btn_void.pack(side="left", padx=4)
        ctk.CTkButton(row, text="更新玩法文件", width=110, command=self._reload_rule).pack(side="left", padx=4)
        ctk.CTkButton(row, text="重试未上报", width=110, command=self._retry_pending_ui).pack(side="left", padx=4)
```

- [ ] **Step 5: 下半区分栏 + 开奖表格卡**（替换原日志卡 L182-188；文件顶部加 `from tkinter import ttk`）：

```python
        # ---- 下半区：左日志 / 右开奖表格 ----
        lower = ctk.CTkFrame(self)
        lower.pack(fill="both", expand=True, padx=4, pady=6)
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)
        card_l = ctk.CTkFrame(lower)
        card_l.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        card_r = ctk.CTkFrame(lower)
        card_r.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        ctk.CTkLabel(card_l, text="玩法日志（发言 / 开奖 / 上报结果）",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=10, pady=(6, 2))
        self.txt_log = ctk.CTkTextbox(card_l, height=240, state="disabled",
                                      font=ctk.CTkFont(size=11))
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        top_r = ctk.CTkFrame(card_r, fg_color="transparent")
        top_r.pack(fill="x", padx=10, pady=(6, 2))
        ctk.CTkLabel(top_r, text="本局开奖表（双击「抢到金额」可修改；下注不可改）｜ 当前群：",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.lbl_table_group = ctk.CTkLabel(top_r, text="", text_color="#2ecc71",
                                            font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_table_group.pack(side="left", padx=4)
        table_frame = ctk.CTkFrame(card_r)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        cols = ("nick", "bet", "amount", "pts", "result")
        heads = ("昵称", "下注(积分)", "抢到金额(元)", "点数", "结算结果")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=8)
        for c, h in zip(cols, heads):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=70 if c != "nick" else 90, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_table_dclick)
        self.lbl_table_tip = ctk.CTkLabel(card_r, text="", text_color="gray", anchor="w",
                                          font=ctk.CTkFont(size=11), wraplength=480, justify="left")
        self.lbl_table_tip.pack(fill="x", padx=10, pady=(0, 4))
```

- [ ] **Step 6: 群切换器 + 表格数据方法**（含 状态行、双击弹窗）

```python
    def _cur_groups(self) -> list[str]:
        """「当前群」可选值：已激活游戏群优先，其次配置的游戏群/监控群。"""
        groups: list[str] = []
        for g in list(self._active_groups) + self.cfg.play_group_list() \
                 + self.cfg.watch_group_list():
            if g not in groups:
                groups.append(g)
        return groups

    def _refresh_group_optmenu(self) -> None:
        groups = self._cur_groups()
        self.opt_group.configure(values=groups or ["（无游戏群）"])
        cur = self.cfg.play_group_current
        if not cur or cur not in groups:
            cur = groups[0] if groups else ""
        self._set_cur_group(cur)

    def _set_cur_group(self, gid: str) -> None:
        if not gid:
            self._cur_group = ""
            self.opt_group.set("（无游戏群）")
        else:
            self._cur_group = gid
            self.opt_group.set(gid)
            if self.cfg.play_group_current != gid:
                self.cfg.play_group_current = gid
                self.save_config(self.cfg)
        self.lbl_table_group.configure(text=self._cur_group or "（未选）")
        self._table_refresh()
        self._gate_buttons()

    def _on_group_picked(self, choice: str) -> None:
        if choice and not choice.startswith("（"):
            self._set_cur_group(choice)

    def _table_rows_for(self, gid: str) -> list[dict]:
        """当前群表格行（live 数据 = 玩法 bet_snapshot + rpg claims 金额）。"""
        if not (self.engine.is_active() and self.rp_game is not None):
            return []
        snap = self.engine.call_rule("bet_snapshot", gid) or []
        if not snap and gid not in self.rp_game.announced:
            return []  # 无局
        ann = self.rp_game.announced.get(gid)
        rows = []
        for r in snap:
            qq = str(r.get("qq") or "")
            c = (ann or {}).get("claims", {}).get(qq) or {}
            amount = c.get("amount")
            rows.append({"qq": qq, "nickname": r.get("nickname") or qq,
                         "bet": "坐庄" if r.get("boss") else str(r.get("amount")),
                         "amount": amount, "pts": self._pts_preview(amount),
                         "edited": bool(c.get("edited")), "boss": bool(r.get("boss")),
                         "result": ""})
        return rows

    @staticmethod
    def _pts_preview(amount) -> str:
        """抢到金额 -> 点数预览（与玩法一致：(a+b)%10；无效显 ?）。"""
        try:
            a = float(amount or 0)
        except (TypeError, ValueError):
            return "?"
        if not (0 < a <= 0.99):
            return "?"
        s = f"{a:.2f}"
        return str((int(s[2]) + int(s[3])) % 10)

    def _render_table(self, rows: list[dict]) -> None:
        """把行渲染进 tree（覆盖旧行）；amount 为空显示「未抢」，结果列原样。"""
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.tree.tag_configure("warn", foreground="#c0392b")
        for r in rows:
            amt = r.get("amount")
            tags = []
            if not isinstance(amt, (int, float)) or not (0 < float(amt) <= 0.99):
                tags.append("warn")
            self.tree.insert("", "end", iid=r["qq"], values=(
                r["nickname"], r["bet"],
                "未抢" if not isinstance(amt, (int, float)) else f"{amt:.2f}",
                r["pts"], r["result"]), tags=tags)

    def _table_refresh(self) -> None:
        """刷新当前群表格与状态提示行。

        有活动本局 → 取 live 行并更新缓存；无活动本局 → 渲染上次缓存（结算后保留展示，
        spec §2.1），直到「开始本局」清空。"""
        gid = self._cur_group
        if not gid or not hasattr(self, "tree"):
            return
        rows = self._table_rows_for(gid) if gid else []
        if rows:
            self._table_rows[gid] = rows
            self._table_live.add(gid)
        elif gid in self._table_rows and self._table_rows[gid]:
            rows = self._table_rows[gid]
            self._table_live.discard(gid)
        else:
            rows = []
        self._render_table(rows)
        self.lbl_table_tip.configure(text=self._cur_round_state_txt(gid))

    def _cur_round_state_txt(self, gid: str) -> str:
        """当前群局状态提示行（供表格下说明条；无局/无玩法给引导文案）。"""
        if not (self.engine.is_active() and self.rp_game is not None):
            return "玩法未激活"
        if gid not in self.rp_game.announced:
            if gid in self._table_rows and self._table_rows[gid]:
                return "上一局已结束（结算结果如上）；点「开始本局」开新局"
            return "无进行中本局：点「开始本局」开局（将 @全体 播报开始消息）"
        info = self.engine.call_rule("seal_info", gid) or {}
        phase = info.get("phase") or "idle"
        rid = info.get("round_id") or ""
        ann = self.rp_game.announced[gid]
        fb = ann.get("first_bill")
        need = int(info.get("claim_need") or 0)
        n_bets = int(info.get("n_bets") or 0)
        if phase == "betting":
            fb_txt = f"｜首红包 {fb['total_num']}/{need} 份" if fb else ""
            return f"下注期 ｜ 局 {rid} ｜ 已下注 {n_bets} 人，需开奖 {need} 人{fb_txt}"
        if ann.get("ready_to_settle"):
            return f"已封盘 ｜ 红包已领完 → 可点「结算」（也可先双击表格改/补开奖金额）"
        if fb:
            return (f"已封盘 ｜ 红包 {fb.get('total_num')} 份（需 {need}）"
                    f"{fb.get('recv_num')} 人已领，领完即可结算；份数不足将自动作废")
        return f"已封盘 ｜ 局 {rid}：等管理员发红包开奖（份数≥{need}，每份 0.01~0.99 元）"

    def _on_table_dclick(self, event=None) -> None:
        """双击表格：仅「抢到金额」列（#3）弹窗改值；未抢者=补值（视同抢到）。"""
        gid = self._cur_group
        if not gid or not (self.rp_game and gid in self.rp_game.announced):
            self._log("✗ 当前群没有可编辑的本局（先「开始本局」）")
            return
        if not event or self.tree.identify_column(event.x) != "#3":
            return
        sel = self.tree.selection()
        if not sel:
            return
        info = self.engine.call_rule("seal_info", gid) or {}
        if info.get("phase") not in ("betting", "sealed"):
            self._log("✗ 当前局不在下注/封盘期，不能改开奖")
            return
        qq = sel[0]
        win = ctk.CTkToplevel(self)
        win.title(f"改开奖金额 - {gid}")
        win.geometry("340x150")
        ctk.CTkLabel(win, text="新抢到金额（元，0.01~0.99，两位小数）：").pack(padx=12, pady=(12, 2))
        entry = ctk.CTkEntry(win)
        entry.pack(padx=12, fill="x")
        ctk.CTkLabel(win, text="只改开奖金额（点数随之变化）；下注不可改；未抢者可在此补值",
                     text_color="gray", font=ctk.CTkFont(size=11)).pack(padx=12, pady=(2, 6))

        def ok() -> None:
            v = entry.get().strip()
            try:
                fv = round(float(v), 2)
            except ValueError:
                self._log("✗ 金额需为数字")
                return
            err = self.rp_game.set_claim_amount(gid, qq, fv)
            if err:
                self._log(f"✗ 改开奖失败：{err}")
                return
            nick = next((r["nickname"] for r in self._table_rows.get(gid, [])
                         if r["qq"] == qq), qq)
            self._log(f"管理员改开奖：群{gid} {nick}({qq}) → {fv:.2f}（点 {self._pts_preview(fv)}）")
            win.destroy()
            self._table_refresh()

        ctk.CTkButton(win, text="确定", width=120, command=ok).pack(pady=6)
        entry.focus_set()
```

- [ ] **Step 7: 四个动作处理器 + 门控 + 自动激活**

按钮门控方法（`_apply_state` 尾部与 `_rounds_refresh` 尾部各调一次；调用处先判 `hasattr(self, "btn_seal")` 防初始化顺序）：

```python
    def _gate_buttons(self) -> None:
        """按当前群局状态开关 停止下注/结算/作废。开始本局按钮随 rp 有无 active 局切换。"""
        if not hasattr(self, "btn_seal"):
            return
        gid = self._cur_group
        active = bool(gid and self.engine.is_active() and self.rp_game is not None)
        info = self.engine.call_rule("seal_info", gid) if active else {}
        phase = (info or {}).get("phase") or "idle"
        has_ann = bool(active and self.rp_game and gid in self.rp_game.announced)
        self.btn_seal.configure(state="normal" if (active and has_ann and phase == "betting") else "disabled")
        self.btn_settle.configure(state="normal" if (active and has_ann and phase == "sealed") else "disabled")
        self.btn_void.configure(state="normal" if (active and has_ann) else "disabled")
```

四个处理器（替换旧 `_start_round`/`_end_round`/`_settle_groups`/`_retry_rp_pending_ui` 引用的旧逻辑；`_retry_rp_pending_ui` 新定义为 `game.retry_pending()` + `_rounds_refresh`；删除 `_toggle_continuous`/`_check_continuous`/`btn_cont`/`_continuous`/`_settle_groups`/`_enable_play`/`_stop_play` 及相关引用）：

```python
    def _clear_round_display(self, gid: str) -> None:
        """开局前清空上一局表格展示缓存。"""
        self._table_rows.pop(gid, None)
        self._table_live.discard(gid)

    def _start_round_cur(self) -> None:
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            self._log("✗ 玩法未激活或未选游戏群（先勾选群并等自动激活）")
            return
        if self.rp_game.has_active_round(gid):
            self._log(f"✗ 群{gid} 已有进行中的本局，先结算或作废")
            return
        self._clear_round_display(gid)
        name = self.cfg.play_rule_name or "复合玩法"
        desc = "大吃小×抢庄：发数字下注；发「撑」抢庄；封盘后管理员发红包定大小"
        seq = int(getattr(self.cfg, "game_round_seq", 0) or 0) + 1
        round_id = f"hongbaojifen_{seq:08d}"
        self.cfg.game_round_seq = seq
        self.save_config(self.cfg)
        self.rp_game.start_round(gid, round_id, name)
        text = self.engine.call_rule("handle_round_start", gid, round_id) or (
            f"游戏开始，游戏名称为{name}，游戏介绍为{desc}，游戏对局id为：{round_id}")
        self._rp_send_announce(gid, text)
        self._log(f"群{gid} 开始本局 {round_id}")
        self.after(0, self._table_refresh)
        self.after(0, self._rounds_refresh)

    def _seal_round_cur(self) -> None:
        """停止下注：玩法 handle_seal 汇总 → rp.seal_round（A3 判定）→ 未作废则 @全体 汇总。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        rate = int(getattr(self.cfg, "game_fee_rate", 20) or 20)
        ann = self.rp_game.announced.get(gid)
        if ann is None:
            self._log(f"✗ 群{gid} 没有进行中的本局（先「开始本局」）")
            return
        res = self.engine.call_rule("handle_seal", gid, rate)
        if not isinstance(res, dict):
            self._log(f"✗ 群{gid} 停止下注失败：玩法无响应")
            return
        ann["rate_permille"] = rate
        if not res.get("ok"):
            # 无人下注/状态不符：玩法已复位，直接播报作废并关局
            self._rp_send_announce(gid, str(res.get("text") or "本局已作废"))
            self.rp_game.void_round(gid, None)
            self._clear_round_display(gid)
            self.after(0, self._rounds_refresh)
            return
        self.rp_game.seal_round(gid)   # A3：封盘时首红包已知不足 → 内部直接作废播报
        if gid not in self.rp_game.announced:  # 已被 seal_round 作废
            self._clear_round_display(gid)
            self.after(0, self._rounds_refresh)
            return
        bc = res.get("banker_check")
        if bc:
            qq = str(bc.get("qq") or "")
            need = int(bc.get("need") or 0)
            balance = self._query_points(qq)  # 30s 缓存查询（spec 开放项默认接受）
            if balance < need:
                void_txt = self.engine.call_rule("handle_void", gid) or (
                    f"庄家 {bc.get('nickname') or qq} 余额不足（{balance} < 需 {need}），"
                    f"本局作废（积分未扣）")
                self._rp_send_announce(gid, void_txt)
                self.rp_game.void_round(gid, None)
                self._clear_round_display(gid)
                self._log(f"群{gid} 庄家余额 {balance} < 需 {need}，本局作废")
                self.after(0, self._rounds_refresh)
                return
        self._rp_send_announce(gid, str(res.get("text") or ""))
        self._log(f"群{gid} 已停止下注（封盘），等管理员发红包开奖")
        self.after(0, self._table_refresh)
        self.after(0, self._rounds_refresh)

    def _settle_round_cur(self) -> None:
        """结算（当前群）：未领完需二次确认（强结 = 未领者按 0 输光，spec D3）。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        info = self.engine.call_rule("seal_info", gid) or {}
        if info.get("phase") != "sealed":
            self._log(f"✗ 群{gid} 未封盘，不能结算（先「停止下注」）")
            return
        ann = self.rp_game.announced.get(gid)
        if ann is None:
            self._log(f"✗ 群{gid} 没有本局会话")
            return
        need = int(info.get("claim_need") or 0)
        fb = ann.get("first_bill")
        if not ann.get("ready_to_settle"):
            if not fb:
                self._log(f"⚠ 群{gid} 尚未见到红包事件，请确认管理员已发红包开奖；"
                          f"或双击表格手动补全部开奖金额后再结算")
                return
            fb_n = int(fb.get("total_num") or 0)
            if fb_n < need:
                self._log(f"✗ 群{gid} 红包 {fb_n} 份 < 需开奖 {need} 人，本局应作废（不能结算）")
                return
            if not ann.get("_force_ok"):  # 第一次点：警告 + 放行标志
                ann["_force_ok"] = True
                self._log(f"⚠ 群{gid} 红包未领完（{fb.get('recv_num')}/{fb_n}），"
                          f"未领者将按 0 结算（输光）；确认则再点一次「结算」")
                return
        self._log_sep()
        self._table_refresh()   # 结算前定格当前开奖表（供结算成功后展示）
        self._set_busy(True)

        def run() -> None:
            try:
                r = self.rp_game.settle_round_now(gid)
            except (ExecutorBanned, OperatorDisabled) as e:
                self.after(0, self._mark_banned, e)
                return
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            if r.get("ok"):
                self.after(0, self._after_settle_ok, gid, r.get("results") or [])
            else:
                self.after(0, self._log,
                           f"✗ 群{gid} 结算失败：{r.get('error')}（可「重试未上报」）")
                self.after(0, self._rounds_refresh)
            self.after(0, self._set_busy, False)

        threading.Thread(target=run, name="play-settle-cur", daemon=True).start()

    def _after_settle_ok(self, gid: str, results: list[dict]) -> None:
        """结算成功：把 delta 填进缓存行（结果列），表格保留展示到下次开局（spec §2.1）。"""
        dmap = {r["qq"]: r for r in results}
        rows = []
        for r in self._table_rows.get(gid, []):
            dr = dmap.get(r["qq"])
            r = dict(r)
            r["result"] = "" if dr is None else (f"{dr['delta']:+d}" if dr["delta"] else "平")
            rows.append(r)
        self._table_rows[gid] = rows
        self._log(f"✓ 群{gid} 本局结算成功并已播报，结果保留在开奖表（下次开局清空）")
        self._table_refresh()
        self._rounds_refresh()

    def _void_round_cur(self) -> None:
        """作废本局（当前群）：玩法 handle_void 文案 → @全体 播报 → 关局，不上报。"""
        gid = self._cur_group
        if not (gid and self.engine.is_active() and self.rp_game is not None):
            return
        if not self.rp_game.has_active_round(gid):
            self._log("当前群没有进行中的本局")
            return
        text = self.engine.call_rule("handle_void", gid) or f"本局 {gid} 已作废（积分未扣）"
        self._rp_send_announce(gid, text)
        self.rp_game.void_round(gid, None)
        self._clear_round_display(gid)
        self._log(f"群{gid} 本局已作废（不上报）")
        self.after(0, self._rounds_refresh)
```

自动激活（替换 `_auto_restore` 主体；删除 `_enable_play`/`_stop_play` 方法本体）。真实骨架对照现行 L1163-1216：封禁预检 → 下载 → 插件 play/config 推送 → activate → `_active_groups`/`_rp_build`/成员同步 → UI 刷新；区别仅在数据源（旧按 cfg.play_rule_id 恢复，新固定拉列表挑复合玩法）与 `self.session = RoundSession(...)` 一行删除（session 保持 None）：

```python
    def _auto_restore(self) -> None:
        """启动后自动激活唯一玩法（复合玩法）：拉总后台 active 玩法列表 → 挑名字含「复合」的
        玩法（没有则取第一条）→ 下载到 plays/rule_{id}.py → 插件转发配置（若有游戏群）→ 激活。
        其余行为（封禁预检/插件推送/成员同步/UI 刷新）与原 _auto_restore 逐行保持一致。"""
        groups = self.cfg.play_group_list()
        self._log("自动激活唯一玩法（复合玩法）…")

        def run() -> None:
            client = self._client()
            banned = self._backend_banned_groups(client)
            if banned & set(groups):
                self.after(0, self._log,
                           f"✗ 自动激活取消：群 {','.join(sorted(banned & set(groups)))} 已被总后台封禁")
                self.after(0, self._apply_state)
                return
            rule_id = int(getattr(self.cfg, "play_rule_id", 0) or 0)
            rule_name = str(self.cfg.play_rule_name or "")
            rows: list[dict] = []
            try:
                rows = client.list_rules() or []
            except BackendError as e:
                self.after(0, self._log, f"⚠ 拉取玩法列表失败: {e}（按上次配置激活）")
            if rows:
                for r in rows:
                    if "复合" in str(r.get("name") or ""):
                        rule_id, rule_name = int(r["id"]), str(r.get("name") or f"玩法#{r['id']}")
                        break
                if not rule_id:
                    r = rows[0]
                    rule_id, rule_name = int(r["id"]), str(r.get("name") or f"玩法#{r['id']}")
            if not rule_id:
                self.after(0, self._log, "✗ 无可用玩法：请先在总后台「玩法管理」上传/启用复合玩法")
                self.after(0, self._apply_state)
                return
            dest = self.cfg.plays_dir() / f"rule_{rule_id}.py"
            if rows:
                try:
                    client.download_rule(rule_id, str(dest))
                except BackendError as e:
                    self.after(0, self._log, f"✗ 下载玩法失败: {e}")
                    self.after(0, self._apply_state)
                    return
            if not dest.is_file():
                self.after(0, self._log, f"✗ 玩法文件缺失（本地缓存也没有）: #{rule_id}")
                self.after(0, self._apply_state)
                return
            if groups:
                callback = f"http://127.0.0.1:{int(self.cfg.play_callback_port or 6101)}/play/msg"
                try:
                    requests.post(self.cfg.plugin_api("play/config"),
                                  json={"enabled": True, "groups": groups,
                                        "callback": callback}, timeout=5)
                except requests.RequestException as e:
                    self.after(0, self._log, f"✗ 插件转发配置失败: {e}")
                    self.after(0, self._apply_state)
                    return
            err = self.engine.activate(rule_id, rule_name or f"玩法#{rule_id}", str(dest))
            if err:
                self.after(0, self._log, f"✗ 玩法激活失败: {err}")
            else:
                name = rule_name or f"玩法#{rule_id}"
                self._active_groups = set(groups)
                self._rp_build(rule_id, name)
                self.cfg.play_rule_id = rule_id
                self.cfg.play_rule_name = name
                self.cfg.play_enabled = True
                self.save_config(self.cfg)
                self.after(0, self._log,
                           f"✓ 复合玩法已自动激活（#{rule_id} {name}）"
                           + (f"｜ 游戏群 {','.join(groups)}" if groups
                              else "｜ 未配置游戏群：在玩法页勾选群后自动转发"))
                if groups and self.cfg.play_auto_register_members:
                    for gid in groups:
                        if not self._sync_members_quiet(client, gid):
                            break
            self.after(0, self._refresh_group_checkboxes)
            self.after(0, self._refresh_group_optmenu)
            self.after(0, self._apply_state)
            self.after(0, self._rounds_refresh)

        threading.Thread(target=run, name="play-autoload", daemon=True).start()

    def _reload_rule(self) -> None:
        """更新玩法文件：从总后台重新下载当前激活玩法（引擎 mtime 热重载生效）。"""
        if not self.engine.is_active():
            self._log("✗ 玩法未激活")
            return
        rid = int(self.engine.active_rule_id)

        def run() -> None:
            try:
                self._client().download_rule(rid, str(self.cfg.plays_dir() / f"rule_{rid}.py"))
                self.after(0, self._log, "✓ 玩法文件已更新（热重载自动生效）")
            except BackendError as e:
                self.after(0, self._log, f"✗ 下载玩法失败: {e}")

        threading.Thread(target=run, name="play-reload", daemon=True).start()
```

**善后清单（逐项对照现行 play_tab.py 落实，缺一不可）：**
- `_rp_build`（L572-589）构造 RedPacketGame 的 kwargs 中追加 `claim_need=lambda gid: int(self.engine.call_rule("claim_need", gid) or 0)`（参数签名已在 Task 3 追加；其余 kwarg 原样保留）
- 旧回调服务（L195 `_start_callback_server` 各 path）与 `_handle_redpacket`（L468）保留：`game.handle_claim(payload)` 后追加 `self.after(0, self._table_refresh)` 与 `self.after(0, self._rounds_refresh)`（红包事件会改 claims/首红包/领完状态）；`payload["rate_permille"] = cfg.game_fee_rate` 保留
- `_handle_one`（L591-678）：开局消息管理员校验、下注预检、engine.handle_message → `{balance}` 占位、`rp_game.record_chat` 全保留；`session`（RoundSession）分支删除创建点后保持 None（原引用分支不触发即可，保留判空不删行，避免误伤）；`_base_play_reply`/查分基础玩法保留
- `_rounds_refresh`（L1239 整方法替换，删 RoundSession/`_last_settle` 行）：

```python
    def _rounds_refresh(self) -> None:
        """各游戏群本局状态总览行（UI 线程；本局有变化后调用）。"""
        if not hasattr(self, "lbl_rounds"):
            return  # 控件尚未构建完成时忽略
        lines = []
        if self.engine.is_active() and self.rp_game is not None:
            for gid in sorted(self._active_groups):
                if gid not in self.rp_game.announced:
                    continue
                info = self.engine.call_rule("seal_info", gid) or {}
                phase = info.get("phase") or "idle"
                rid = info.get("round_id") or ""
                ann = self.rp_game.announced[gid]
                fb = ann.get("first_bill")
                if phase == "betting":
                    tail = f"｜已下注 {info.get('n_bets')} 人" if int(info.get("n_bets") or 0) else ""
                    tail += f"｜首红包 {fb['total_num']} 份" if fb else ""
                    lines.append(f"群{gid}：{rid} 下注期{tail}")
                elif fb and int(fb.get("total_num") or 0) > 0:
                    st = "领完可结算" if ann.get("ready_to_settle") else \
                        f"已领 {fb.get('recv_num')}/{fb.get('total_num')}"
                    lines.append(f"群{gid}：{rid} 封盘｜{st}（红包不足自动作废）")
                else:
                    lines.append(f"群{gid}：{rid} 封盘｜等管理员发红包开奖")
        if lines:
            self.lbl_rounds.configure(text="\n".join(lines), text_color="#2ecc71")
        else:
            self.lbl_rounds.configure(text="", text_color="gray")
        for g, lbl in getattr(self, "_group_labels", {}).items():
            if self.engine.is_active() and g in self._active_groups:
                if g in (self.rp_game.announced if self.rp_game else {}):
                    lbl.configure(text="本局中", text_color="#2ecc71")
                else:
                    lbl.configure(text="待开局", text_color="gray")
            elif self.engine.is_active():
                lbl.configure(text="未参与", text_color="gray")
            else:
                lbl.configure(text="", text_color="gray")
        self._gate_buttons()
```
- `_refresh_group_checkboxes`（L715）与 `_on_group_toggled`（L758）/`_push_play_groups`（L748）保留；勾选变化后调 `_refresh_group_optmenu`
- `_apply_state`（L1279）尾部调 `_gate_buttons()` 与 `_refresh_group_optmenu()`（首次构建后）
- `_mark_banned`（L1134）：置 `rp_game = None` 之外补 `self._cur_group = ""`、清空表格、`_gate_buttons()`
- `_maybe_retry_rp_pending`（L294，worker 每 30s）：保留（内部 `retry_pending()` 已含 Task 3 Step 8 的清局）；成功后刷新
- 顶栏帮助文案 HELP_TEXT（main_window.py）与玩法页自述如需提及「启用玩法/连续开局」的地方改述为唯一复合玩法操作台（随任务 6 文案同步）

- [ ] **Step 8: 冒烟验证**

Run（`D:\work\hongbaojifen\agent`）:
`python -m py_compile gui/app/play_tab.py gui/app/config_manager.py integration/play_engine.py integration/redpacket_game.py play_rules/rule_fuhe.py`
Expected: 无语法错误。（GUI 真实按钮流验证在任务 6 手工全链路；本任务关口 = 编译 + 逐项代码评审。）

---

### Task 5: 总后台收敛（只留复合玩法）+ agent/play_rules 清理 + 双仓库同步

**Files:**
- Modify（Java）: `src/main/java/com/hbjf/api/service/PlayRuleService.java`（SEED_RULES 数组 + ensureSeedRules 方法）
- Modify（资源）: `src/main/resources/seed_rules/`（删 rule_add1.py/rule_add2.py/rule_redpacket.py，新增 rule_fuhe.py）
- 删除（agent）: `agent/play_rules/rule_add1.py`、`rule_add2.py`、`rule_redpacket.py`、`rule_dcxx.py`、`rule_laoda.py`（只留 rule_fuhe.py）
- 数据库: play_rule_files 收敛 + `data/rules/` 文件落盘（无迁移，SQL 直改）

**Interfaces:**
- Consumes: Task 1 的 `agent/play_rules/rule_fuhe.py`（本任务以它为唯一规则内容源）
- Produces: 总后台唯一 active 玩法 = 复合玩法；seed 兜底保证全新环境也有一份
- 已确认：PlayRuleService 构造注入 `JdbcTemplate jdbc`（L42-45）+ `AppProperties props`；`FMT` 常量 L33；服务现用 jdbc 查询/插入（upload 同款风格）

- [ ] **Step 1: Java 侧只保留复合玩法种子**（PlayRuleService.java L36-40 SEED_RULES 替换 + ensureSeedRules L61-80 整方法替换；先读现方法体再落笔）：

```java
    /** 随 jar 内置的唯一默认玩法（classpath:seed_rules/rule_fuhe.py）。复合玩法元数据行
     *  由 ensureSeedRules 幂等补齐（收敛前由迁移/手动插入的旧玩法行见收敛 SQL）。 */
    private static final String[][] SEED_RULES = {
            {"seed_rules/rule_fuhe.py", "seed_rule_fuhe.py"},
    };
```

```java
    /** 启动兜底：classpath 默认玩法落盘 rules-dir（已存在不覆盖 —— 不覆盖用户重传版本），
     *  并幂等补齐 play_rule_files 元数据行（name='复合玩法' 的 active 行存在则跳过）。
     *  失败只告警，不阻断启动。 */
    @PostConstruct
    public void ensureSeedRules() {
        for (String[] pair : SEED_RULES) {
            try {
                ClassPathResource cp = new ClassPathResource(pair[0]);
                if (!cp.exists()) {
                    log.warn("默认玩法资源缺失，跳过: {}", pair[0]);
                    continue;
                }
                File target = new File(rulesDir().getAbsoluteFile(), pair[1]);
                if (!target.exists()) {
                    try (InputStream in = cp.getInputStream()) {
                        Files.copy(in, target.toPath());
                    }
                    log.info("默认玩法已落盘: {}", target.getAbsolutePath());
                }
                Integer cnt = jdbc.queryForObject(
                        "SELECT COUNT(*) FROM play_rule_files WHERE name='复合玩法' AND status='active'",
                        Integer.class);
                if (cnt == null || cnt == 0) {
                    String now = LocalDateTime.now().format(FMT);
                    jdbc.update("INSERT INTO play_rule_files "
                                    + "(name, description, file_name, file_size, version, status, created_at, updated_at) "
                                    + "VALUES (?,?,?,?,?,?,?,?)",
                            "复合玩法", "大吃小×抢庄（唯一玩法）：封盘后管理员发红包定大小，发「撑」抢庄",
                            target.getName(), target.length(), "1.0", "active", now, now);
                    log.info("默认玩法元数据行已补齐: 复合玩法 file_name={}", target.getName());
                }
            } catch (Exception e) {
                log.warn("默认玩法兜底失败: {}", pair[0], e);
            }
        }
    }
```
（若现有 ensureSeedRules 的规则目录 mkdir/相对路径处理与此不同，以现有实现的文件处理为准、只替换插入元数据语义；INSERT 列名先对照表结构确认 —— 本仓库 play_rule_files 列名沿用 upload 的 insert 语句。）

- [ ] **Step 2: 新资源落位**：把 Task 1 的 `agent/play_rules/rule_fuhe.py` 复制为 `src/main/resources/seed_rules/rule_fuhe.py`；删除 `src/main/resources/seed_rules/rule_add1.py|rule_add2.py|rule_redpacket.py`。

- [ ] **Step 3: Java 编译/测试**

Run（`D:\work\hongbaojifen`）: `mvn -q -Dskip.fe=true test`
Expected: BUILD SUCCESS。
⚠ 记忆教训：mvn test 会把 src/main/resources 全量拷入 target/classes——src 已删净旧 seed 即可，target 残留不影响（启动只读 classpath src）；**本次无新增迁移**，重启不应补执行任何 Flyway。

- [ ] **Step 4: 重启 8892**（按记忆流程）并在日志确认「默认玩法已落盘 / 元数据行已补齐 / 已存在不补」其一。

- [ ] **Step 5: 数据库收敛**

执行（mysql CLI 或总后台管理页手工 + 核对 SQL 双保险；先留档再删）：
```sql
SELECT id, name, file_name, status FROM play_rule_files ORDER BY id;
```
把每一行 name≠复合玩法 的 `file_name` 记下，然后：
- 删除对应 `data/rules/<file_name>.py` 磁盘文件（用 git-bash `rm`）
- SQL: `DELETE FROM play_rule_files WHERE name <> '复合玩法';`
- 复合玩法行若已由 ensureSeedRules 补齐则核对；否则手工补（同 Step 1 的 INSERT 内容，file_name=seed_rule_fuhe.py）
- 核对: `SELECT COUNT(*) FROM play_rule_files WHERE status='active';` = 1；`ls data/rules/` 只应有 seed_rule_fuhe.py（agent 之后重传会再产生一个 UUID 复合玩法文件，正常）

- [ ] **Step 6: agent/play_rules 收敛 + 双仓库同步 + 下载端点验证**

- 删除 `agent/play_rules/rule_add1.py|rule_add2.py|rule_redpacket.py|rule_dcxx.py|rule_laoda.py`
- 把 `agent/play_rules/rule_fuhe.py` 复制为 `D:\work\hongbaojifen\data\rules\seed_rule_fuhe.py`，`md5sum` 两文件比对一致
- 若 Step 5 之后用户在总后台通过管理页重新上传/保存过复合玩法（生成新 UUID 文件名），以 DB 行引用的文件为准重新同步 agent 副本（`SELECT file_name` 先查）
- `curl -s http://localhost:8892/api/open/rules/<复合玩法id>/download | md5sum` 与 agent 副本一致（status=active 才允许下载）

- [ ] **Step 7: agent 侧冒烟**：`python play_rules/rule_fuhe.py` + `python -m integration.redpacket_game` 仍全绿。

---

### Task 6: 版本/文档同步 + 手工全链路验证清单

**Files:**
- Modify: `agent/gui/app/main_window.py` L29（APP_VERSION → `2026.09.06-12`；HELP_TEXT 玩法段改复合玩法描述）
- Modify: `agent/docs/代码架构说明.md`（行 4 版本引用；§8 玩法列表只留复合玩法；玩法协议新函数表补 handle_seal/handle_void/claim_need/bet_snapshot/seal_info + call_rule）
- 可选 Modify: `agent/docs/玩法系统设计.md`、`玩法v2结算与上报设计.md`（头部加一行「2026-09-06 起由复合玩法重做取代，本文仅留档」即可，不重写）

- [ ] **Step 1: 版本 bump + 架构文档同步**（记忆规则：APP_VERSION 与 代码架构说明.md 行 4 同步，玩法列表 §8 更新）
- [ ] **Step 2: 全量冒烟**（agent）：`python -m py_compile`（四个 Python 文件）+ `python play_rules/rule_fuhe.py` + `python -m integration.play_engine` + `python -m integration.redpacket_game` 全绿；总后台 `mvn -q -Dskip.fe=true test` 通过（不需重启，无 Java 改动）
- [ ] **Step 3: 手工全链路（与用户一起验收，逐条勾）**：

1. 启动 agent → 玩法页自动激活「复合玩法」（日志 ✓），群勾选/当前群可选
2. 选当前群 → 「开始本局」→ 群内 @全体 收到「游戏开始！本局对局编号：hongbaojifen_xxxx…」
3. 玩家甲发 `100`、乙发 `20` → 各自收到累计名单回复；「查分」可用
4. 玩家发「撑」→ 抢庄公告（已下注者提示不能抢庄）
5. 「停止下注」→ 群内封盘汇总（人数/注额/合计/红包要求份数）
6. 管理员发红包 → 玩家领 → 回执带点数；第 2 个红包事件被忽略（日志「只认第 1 个红包」）
7. 红包份数 < 需开奖人数 → 自动作废播报 → 重开局后积分无变动
8. 正常局领完 → 提示可结算；双击改某玩家开奖金额 → 表格点数即时变 + 日志「管理员改开奖」
9. 「结算」→ 每人收到个人开奖回复 + @全体 结算统计；总后台对局记录 Σ=−抽水，与玩法文件一致
10. 抢庄局：庄家余额不足 → 封盘即作废；正常抢庄局 → 庄家承担抽水，各挑战者 vs 庄家结算
11. 多群同时两局互不干扰（表格/按钮只作用当前群；切群刷新）
12. 「作废本局」→ 播报作废；「更新玩法文件」→ 热重载生效
13. 总后台「玩法管理」列表只剩「复合玩法」一行；play_rule_files active=1
14. 退出重开 agent → 自动激活恢复（配置持久化）
15. 修改玩法文件 → agent「更新玩法文件」→ 生效（验证双仓库同步闭环）

- [ ] **Step 4: 汇报用户**：改动摘要 + 验收清单 + 需要用户配合的事项（如总后台管理页核对复合玩法行）。**不 git commit**（留待用户评审后统一提交）。
