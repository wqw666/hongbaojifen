#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按群号 + 时间范围查询红包领取详情。

依赖：本机已运行 NapCat，并加载 napcat-plugin-cleaner。

默认插件 HTTP 路径（NapCat WebUI 同源，注意端口）：
  http://127.0.0.1:6099/plugin/napcat-plugin-cleaner/api/query

用法：
  python query.py --group 123456789 --start "2026-03-26 12:00:00" --end "2026-03-26 13:00:00"
  python query.py --group 123456789 --start 1711411200 --end 1711414800
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description="查询群红包领取详情")
    p.add_argument("--base", default="http://127.0.0.1:6099", help="NapCat WebUI 地址")
    p.add_argument("--plugin", default="napcat-plugin-cleaner", help="插件目录名")
    p.add_argument("--group", required=True, help="群号")
    p.add_argument("--start", required=True, help="开始时间 unix秒 或 可读时间")
    p.add_argument("--end", required=True, help="结束时间")
    p.add_argument("--no-refresh", action="store_true", help="不重新 pullDetail，只用本地缓存")
    args = p.parse_args()

    qs = urllib.parse.urlencode(
        {
            "group_id": args.group,
            "start": args.start,
            "end": args.end,
            "refresh": "false" if args.no_refresh else "true",
        }
    )
    url = f"{args.base.rstrip('/')}/plugin/{args.plugin}/api/query?{qs}"

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"请求失败: {e}", file=sys.stderr)
        print(f"URL: {url}", file=sys.stderr)
        return 1

    print(json.dumps(data, ensure_ascii=False, indent=2))

    if data.get("code") != 0:
        return 2

    packets = (data.get("data") or {}).get("packets") or []
    if not packets:
        print("\n该时间范围内未找到红包记录（需机器人账号曾收到该群红包消息）。", file=sys.stderr)
        return 0

    print("\n===== 领取明细 =====")
    for pkt in packets:
        print(
            f"\n红包 {pkt.get('bill_no')} | 群 {pkt.get('group_name')}({pkt.get('group_id')}) "
            f"| 发送者 {pkt.get('sender_name')}({pkt.get('sender_uin')}) "
            f"| 时间 {pkt.get('msg_time_text')}"
        )
        claims = pkt.get("claims") or []
        if not claims:
            print("  (暂无领取详情，可去掉 --no-refresh 再试)")
            continue
        for c in claims:
            print(
                f"  - {c.get('name') or '-'}({c.get('uin')})  "
                f"{c.get('amount')} 元  {c.get('time_text')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
