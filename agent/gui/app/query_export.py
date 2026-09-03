"""历史查询结果导出为 CSV 表格（Excel 可直接打开）。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CSV_COLUMNS = [
    "群号",
    "群名",
    "红包单号",
    "发送时间",
    "发送者QQ",
    "发送者昵称",
    "祝福语",
    "我已领取",
    "我的金额(元)",
    "已领份数",
    "总份数",
    "已领金额(元)",
    "总金额(元)",
    "手气最佳",
    "领取人QQ",
    "领取人昵称",
    "领取金额(元)",
    "领取时间",
]


def _fmt_amount(v: Any) -> str:
    if v in (None, "", 0, 0.0):
        return ""
    return str(v)


def flatten_query_rows(data: dict[str, Any]) -> list[dict[str, str]]:
    """每个领取人一行；无领取明细时保留一行红包信息。"""
    gid = str(data.get("group_id") or "")
    rows: list[dict[str, str]] = []

    for pkt in data.get("packets") or []:
        summary = pkt.get("summary") or {}
        base = {
            "群号": gid,
            "群名": str(pkt.get("group_name") or ""),
            "红包单号": str(pkt.get("bill_no") or ""),
            "发送时间": str(pkt.get("msg_time_text") or pkt.get("msg_time") or ""),
            "发送者QQ": str(pkt.get("sender_uin") or ""),
            "发送者昵称": str(pkt.get("sender_name") or ""),
            "祝福语": str(pkt.get("wishing") or ""),
            "我已领取": "是" if pkt.get("grabbed") else "否",
            "我的金额(元)": _fmt_amount(pkt.get("my_amount")),
            "已领份数": str(summary.get("recv_num") if summary.get("recv_num") is not None else ""),
            "总份数": str(summary.get("total_num") if summary.get("total_num") is not None else ""),
            "已领金额(元)": _fmt_amount(summary.get("recv_amount")),
            "总金额(元)": _fmt_amount(summary.get("total_amount")),
            "手气最佳": str(summary.get("lucky_name") or summary.get("lucky_uin") or ""),
        }
        claims = pkt.get("claims") or []
        if not claims:
            rows.append(
                {
                    **base,
                    "领取人QQ": "",
                    "领取人昵称": "",
                    "领取金额(元)": "",
                    "领取时间": "",
                }
            )
            continue
        for c in claims:
            rows.append(
                {
                    **base,
                    "领取人QQ": str(c.get("uin") or ""),
                    "领取人昵称": str(c.get("name") or ""),
                    "领取金额(元)": _fmt_amount(c.get("amount")),
                    "领取时间": str(c.get("time_text") or c.get("time") or ""),
                }
            )
    return rows


def write_query_csv(data: dict[str, Any], path: str | Path) -> int:
    """写入 UTF-8 BOM CSV，返回导出行数。"""
    rows = flatten_query_rows(data)
    path = Path(path)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
