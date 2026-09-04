"""冒烟测试：验证 agent → 总后台全链路对接可用。

用法（总后台需已启动、已建执行器拿到 token）：
    python -m integration.smoke --base http://localhost:8892 --key hbjf-open-2026 --token <执行器token>
可选：
    --self-qq <QQ>         顺带验证操作员QQ上报（心跳带 admin_qq 自动登记 与 手动 upsert 两条路）
    --self-nick S          操作员QQ昵称（默认 冒烟测试号）
    --member-count N       用假成员数据跑一次会员同步流程（默认 0 不跑）
    --member-prefix S      假成员 QQ 前缀（默认 50000，会注册 N 个测试会员到总后台！）
    --play-events N        用假事件跑一次对局上报 + 同 round_id 重复上报幂等验证（默认 0 不跑）

注意：会员/对局部分会真实注册测试数据到总后台，仅建议对本机开发库执行。
"""
from __future__ import annotations

import argparse
import sys
import time

from .backend_client import BackendError, HbjfClient
from . import member_sync

# Windows GBK 控制台/管道打不出 ✓/✗ 与中文：统一 UTF-8 容错输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def _step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'  ✓' if ok else '  ✗'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="agent-总后台对接冒烟测试")
    ap.add_argument("--base", default="http://localhost:8892", help="总后台地址")
    ap.add_argument("--key", default="hbjf-open-2026", help="X-Api-Key 对接密钥")
    ap.add_argument("--token", required=True, help="执行器 token")
    ap.add_argument("--self-qq", default="", help="本机登录 QQ（验证操作员号上报）")
    ap.add_argument("--self-nick", default="冒烟测试号", help="操作员QQ昵称")
    ap.add_argument("--member-count", type=int, default=0, help="跑 N 个假成员的会员同步（默认 0）")
    ap.add_argument("--member-prefix", default="50000", help="假成员 QQ 前缀")
    ap.add_argument("--play-events", type=int, default=0,
                    help="用 N 条假事件跑对局上报+幂等验证（默认 0 不跑）")
    args = ap.parse_args()

    client = HbjfClient(args.base, args.key, args.token)
    ok = True

    # 1) 心跳（带本机登录QQ → 总后台自动登记/续活操作员并记录登录时间）
    try:
        r = client.heartbeat(host="smoke", version="smoke-1.0",
                             admin_qq=args.self_qq, admin_nickname=args.self_nick)
        ok &= _step("心跳", True, f"data={r}")
    except BackendError as e:
        ok &= _step("心跳", False, str(e))

    # 2) QQ 群上报 + 列表
    try:
        client.upsert_group("999999999", group_name="冒烟测试群", member_count=1)
        groups = client.list_groups()
        found = any(str(g.get("group_id")) == "999999999" for g in groups)
        ok &= _step("QQ群上报+列表", found, f"群数={len(groups)}")
    except BackendError as e:
        ok &= _step("QQ群上报+列表", False, str(e))

    # 3) 操作员 QQ 手动上报（可选；心跳自动登记那条路已在第 1 步验证）
    if args.self_qq:
        try:
            r = client.upsert_qq_account(args.self_qq, nickname=args.self_nick, remark="smoke")
            rows = client.list_qq_accounts()
            found = any(str(g.get("qq")) == args.self_qq for g in rows)
            ok &= _step("操作员QQ上报", found,
                        f"created={r.get('created')} 操作员数={len(rows)}")
        except BackendError as e:
            ok &= _step("操作员QQ上报", False, str(e))

    # 4) 会员同步（可选，会真实注册测试会员）
    if args.member_count > 0:
        fake_members = [{"qq": f"{args.member_prefix}{i:05d}", "nickname": f"冒烟{i}"}
                        for i in range(args.member_count)]
        try:
            result = member_sync.run_member_sync(
                client, "999999999",
                members_provider=lambda gid: fake_members,
                on_progress=lambda msg: print(f"  · {msg}"))
            ok &= _step("会员同步", result.get("warning") == "",
                        f"total={result.get('total')} added={result.get('added')} "
                        f"existed={result.get('existed')} warning={result.get('warning') or '无'}")
        except (BackendError, Exception) as e:  # noqa: BLE001 — smoke 要报全
            ok &= _step("会员同步", False, str(e))
    else:
        print("  · 跳过会员同步（--member-count 0）")

    # 5) 命令通道 poll（通常为空；已回报与未下发的区分以总后台下发为准）
    try:
        cmds = client.poll_commands()
        ok &= _step("命令poll", isinstance(cmds, list), f"待执行={len(cmds)}")
    except BackendError as e:
        ok &= _step("命令poll", False, str(e))

    # 6) 对局上报 + round_id 幂等（可选，会真实写入游戏记录/回放入账）
    if args.play_events > 0:
        round_id = "smoke-" + time.strftime("%Y%m%d-%H%M%S")
        events = [{"qq": f"{args.member_prefix}{i:05d}", "nickname": f"冒烟{i}",
                   "msg": f"冒烟发言{i}", "reply": f"冒烟回复{i}",
                   "delta": 1 if i % 2 == 0 else -1} for i in range(args.play_events)]
        try:
            r = client.report_game_round(round_id, "冒烟玩法", "999999999", events)
            dup = client.report_game_round(round_id, "冒烟玩法", "999999999", events)
            ok &= _step("对局上报+幂等",
                        r.get("duplicate") is False and dup.get("duplicate") is True,
                        f"round={r.get('round_id')} events={r.get('event_count')} "
                        f"member_count={r.get('member_count')} total_delta={r.get('total_delta')} "
                        f"warning={r.get('warning') or '无'}；重复上报 duplicate={dup.get('duplicate')}")
        except BackendError as e:
            ok &= _step("对局上报+幂等", False, str(e))
    else:
        print("  · 跳过对局上报（--play-events 0）")

    print("\n冒烟测试" + ("通过 ✓" if ok else "存在失败 ✗，详见上方"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
