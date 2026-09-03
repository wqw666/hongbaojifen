"""冒烟测试：验证 agent → 总后台全链路对接可用。

用法（总后台需已启动、已建执行器拿到 token）：
    python -m integration.smoke --base http://localhost:8892 --key hbjf-open-2026 --token <执行器token>
可选：
    --self-qq <QQ>        顺带验证管理员 QQ 上报
    --member-count N      用假成员数据跑一次会员同步流程（默认 0 不跑）
    --member-prefix S     假成员 QQ 前缀（默认 50000，会注册 N 个测试会员到总后台！）

注意：会员同步部分会真实注册测试会员到总后台，仅建议对本机开发库执行。
"""
from __future__ import annotations

import argparse
import sys

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
    ap.add_argument("--self-qq", default="", help="本机登录 QQ（验证管理员号上报）")
    ap.add_argument("--member-count", type=int, default=0, help="跑 N 个假成员的会员同步（默认 0）")
    ap.add_argument("--member-prefix", default="50000", help="假成员 QQ 前缀")
    args = ap.parse_args()

    client = HbjfClient(args.base, args.key, args.token)
    ok = True

    # 1) 心跳
    try:
        r = client.heartbeat(host="smoke", version="smoke-1.0")
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

    # 3) 管理员 QQ 上报（可选）
    if args.self_qq:
        try:
            r = client.upsert_qq_account(args.self_qq, qq_type="admin_qq",
                                         nickname="冒烟测试号", remark="smoke")
            rows = client.list_qq_accounts("admin_qq")
            found = any(str(g.get("qq")) == args.self_qq for g in rows)
            ok &= _step("管理员QQ上报", found, f"created={r.get('created')} admin数={len(rows)}")
        except BackendError as e:
            ok &= _step("管理员QQ上报", False, str(e))

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

    print("\n冒烟测试" + ("通过 ✓" if ok else "存在失败 ✗，详见上方"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
