# -*- coding: utf-8 -*-
"""总后台 v1.0.4 全链路 E2E（对本地 MySQL 库真实写入，可重复执行：执行器名带时间戳）。"""
import json
import sys
import time

import requests

# Windows GBK 控制台/管道打不出 ✓/✗ 与中文：统一 UTF-8 容错输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "http://localhost:8892"
KEY = "hbjf-open-2026"
S = requests.Session()
S.headers["X-Api-Key"] = KEY
S.headers["content-type"] = "application/json"

_results: list[tuple[str, bool, str]] = []


def step(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    print(("  ✓ " if cond else "  ✗ ") + name + (f" — {detail}" if detail else ""))


def jcall(method: str, path: str, **kw) -> tuple[int, dict]:
    try:
        r = S.request(method, BASE + path, timeout=15, **kw)
    except requests.RequestException as e:
        return 0, {"message": str(e)}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text[:200]}


def _list_of(data: dict) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        lst = data.get("list")
        return lst if isinstance(lst, list) else []
    return []


def main() -> int:
    ts = time.strftime("%H%M%S")

    # 1) 管理员登录
    code, body = jcall("POST", "/api/auth/login", json={"username": "admin", "password": "admin123"})
    tok = (body.get("data") or {}).get("token")
    step("管理员登录", code == 200 and bool(tok), f"http={code}")
    AH = {"Authorization": f"Bearer {tok}"}

    # 2) 新增执行器（一次性 token）
    code, body = jcall("POST", "/api/admin/executors", headers=AH,
                       json={"name": f"E2E执行器-{ts}", "version": "e2e-1.0", "host": "e2e-host"})
    data = body.get("data") or {}
    etoken = str(data.get("token_plain") or data.get("token") or "")
    eid = data.get("id")
    step("新增执行器(取一次性token)", code == 200 and bool(etoken) and eid,
         f"id={eid} http={code}")

    # 3) 心跳带 admin_qq → 自动登记操作员（最近登录时间/IP 落库）
    code, body = jcall("POST", "/api/open/executor/heartbeat",
                       json={"token": etoken, "host": "e2e-host", "version": "e2e-1.0",
                             "admin_qq": "60001", "admin_nickname": "E2E操作员"})
    step("心跳(带admin_qq)", code == 200 and body.get("code") == 0,
         json.dumps(body.get("data") or {}, ensure_ascii=False)[:160])
    _, b = jcall("GET", "/api/open/qq-accounts")
    ops = [x for x in (b.get("data") or []) if str(x.get("qq")) == "60001"]
    op = ops[0] if ops else None
    step("心跳自动登记操作员(登录时间IP/权限)",
         op is not None and op.get("status") == "active"
         and op.get("can_manual_points") == "allowed"
         and bool(op.get("last_login_at")) and bool(op.get("last_login_ip")),
         json.dumps(op, ensure_ascii=False)[:200] if op else "未找到60001")

    # 4) 群上报：带 create_time + executor_token（校验 19 位格式、绑定执行器）
    code, body = jcall("POST", "/api/open/groups",
                       json={"group_id": "77770001", "group_name": "E2E玩法群",
                             "owner_qq": "60001", "admin_qqs": "60001", "member_count": 3,
                             "create_time": "2020-01-02 03:04:05", "executor_token": etoken})
    _, b = jcall("GET", "/api/open/groups")
    grp = next((g for g in (b.get("data") or []) if str(g.get("group_id")) == "77770001"), None)
    step("群上报(创建时间/绑定)",
         grp is not None and str(grp.get("create_time") or "") == "2020-01-02 03:04:05"
         and grp.get("status") == "active",
         json.dumps(grp, ensure_ascii=False)[:200] if grp else str(body))

    # 5) 会员批量注册（注册人 = 绑定操作员 60001）
    code, body = jcall("POST", "/api/open/members/batch",
                       json={"members": [{"qq": "60010", "nickname": "阿十", "group_id": "77770001"},
                                         {"qq": "60011", "nickname": "小十一"}],
                             "executor_token": etoken})
    d = body.get("data") or {}
    step("会员批量注册(幂等)", code == 200 and body.get("code") == 0
         and (d.get("added", 0) + d.get("existed", 0)) == 2,
         json.dumps(d, ensure_ascii=False))

    # 6) 批量查分：60010/60011 exists，60012 未建档
    _, b = jcall("GET", "/api/open/points/batch", params={"qqs": "60010,60011,60012"})
    lst = _list_of(b.get("data") or {})
    m = {str(x.get("qq")): x for x in lst}
    step("批量查分",
         m.get("60010", {}).get("exists") is True
         and m.get("60011", {}).get("exists") is True
         and m.get("60012", {}).get("exists") is False,
         json.dumps(lst, ensure_ascii=False)[:200])

    # 7) 对局上报：+5 / +2 / 未知会员 60099 → warning，时间线照记
    # 先取 60010/60011 的当前积分作基线（多次运行脚本时只验证本次变动）
    _, b0 = jcall("GET", "/api/open/points/batch", params={"qqs": "60010,60011"})
    base = {str(x.get("qq")): x.get("points") or 0 for x in _list_of(b0.get("data") or {})}
    round_id = f"e2e-{time.strftime('%m%d')}-{ts}"
    events = [
        {"qq": "60010", "nickname": "阿十", "msg": "2*3=?", "reply": "6", "delta": 5},
        {"qq": "60011", "nickname": "小十一", "msg": "4+4=?", "reply": "8", "delta": 2},
        {"qq": "60099", "nickname": "路人", "msg": "也来一个", "reply": "", "delta": 1},
    ]
    code, body = jcall("POST", "/api/open/games/report",
                       json={"executor_token": etoken, "round_id": round_id,
                             "group_id": "77770001", "play_name": "E2E算术", "events": events})
    d = body.get("data") or {}
    step("对局上报(入账/警告)", code == 200 and body.get("code") == 0
         and d.get("event_count") == 3 and d.get("total_delta") == 7
         and d.get("warning_count") == 1 and d.get("member_count") == 2,
         json.dumps(d, ensure_ascii=False)[:220])
    code, body = jcall("POST", "/api/open/games/report",
                       json={"executor_token": etoken, "round_id": round_id,
                             "group_id": "77770001", "play_name": "E2E算术", "events": events})
    step("重复上报幂等", code == 200 and (body.get("data") or {}).get("duplicate") is True,
         json.dumps(body.get("data") or {}, ensure_ascii=False)[:160])
    _, b = jcall("GET", "/api/open/points/batch", params={"qqs": "60010,60011"})
    m2 = {str(x.get("qq")): x.get("points") for x in _list_of(b.get("data") or {})}
    step("积分实时入账(本次+5/+2)",
         m2.get("60010") == base.get("60010", 0) + 5
         and m2.get("60011") == base.get("60011", 0) + 2,
         f"基线 {base} → {m2}")

    # 8) 管理端游戏记录 + 回放
    code, body = jcall("GET", "/api/admin/game-records", headers=AH,
                       params={"group_id": "77770001", "page": 1, "size": 10})
    data = body.get("data") or {}
    rec = next((r for r in (data.get("list") or []) if r.get("round_id") == round_id), None)
    ok_replay = False
    if rec:
        _, b = jcall("GET", f"/api/admin/game-records/{rec.get('id')}", headers=AH)
        evs = ((b.get("data") or {}).get("events")) or []
        ok_replay = (len(evs) == 3 and str(evs[0].get("qq")) == "60010"
                     and evs[0].get("delta") == 5)
    step("管理端记录+回放", bool(rec) and ok_replay and rec.get("operator_qq") == "60001",
         json.dumps(rec, ensure_ascii=False)[:200] if rec else "未找到该局")

    # 9) 封禁执行器 → 心跳 40310 停摆；解封 → 恢复
    code, body = jcall("POST", f"/api/admin/executors/{eid}/ban", headers=AH, json={})
    code, body = jcall("POST", "/api/open/executor/heartbeat",
                       json={"token": etoken, "host": "e2e-host"})
    step("封禁后心跳被拒(40310)", body.get("code") == 40310 or code >= 400,
         json.dumps(body, ensure_ascii=False)[:160])
    code, body = jcall("POST", f"/api/admin/executors/{eid}/unban", headers=AH, json={})
    code, body = jcall("POST", "/api/open/executor/heartbeat",
                       json={"token": etoken, "host": "e2e-host", "admin_qq": "60001"})
    step("解封后心跳恢复", code == 200 and body.get("code") == 0,
         json.dumps(body.get("data") or {}, ensure_ascii=False)[:120])

    failed = [r for r in _results if not r[1]]
    print("\nE2E " + ("全部通过 ✓" if not failed
                      else f"存在失败 ✗（{len(failed)}/{len(_results)}）"))
    for name, _c, detail in failed:
        print(f"  失败项: {name} — {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
