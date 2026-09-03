"""会员同步：把「会员群」全部成员注册到总后台会员库并拉取积分。

流程（幂等，可反复执行）：
1. 取群成员列表（provider 注入，GUI 里接 PluginClient）
2. 上报群信息（群名/群主/管理员/人数）→ /api/open/groups upsert
3. 批量查积分（500/片）→ 已建档的跳过
4. 未建档成员批量注册（500/片）→ /api/open/members/batch
5. 返回统计 {total, added, existed, skipped, warning}

本模块不 import GUI/插件，只认 duck-typed provider：
- members_provider(group_id) -> [{qq, nickname?, role?}...]  （role 含 owner/admin/member）
- group_provider(group_id)   -> {group_name?, owner_qq?, admin_qqs?} （可为 None 跳过）
"""
from __future__ import annotations

from .backend_client import HbjfClient, BackendError

QUERY_CHUNK = 500   # 批量查积分上限
REGISTER_CHUNK = 500  # 批量注册上限


def _member_qq(m: dict) -> str:
    """兼容 qq / user_id / uin 三种字段名的 QQ 提取，非法返回 ''。"""
    v = str(m.get("qq") or m.get("user_id") or m.get("uin") or "").strip()
    return v if v.isdigit() else ""


def _member_nick(m: dict) -> str:
    return str(m.get("nickname") or m.get("card") or "").strip()


def run_member_sync(client: HbjfClient, group_id: str, members_provider, group_provider=None,
                    on_progress=None) -> dict:
    """同步一个群的成员为会员。on_progress(msg: str) 可选回调（每阶段调一次）。"""
    if on_progress:
        on_progress(f"拉取群 {group_id} 成员…")
    members = members_provider(group_id) or []
    # 去重 + 只留合法 QQ
    seen: set[str] = set()
    valid = []
    for m in members:
        qq = _member_qq(m)
        if qq and qq not in seen:
            seen.add(qq)
            valid.append({"qq": qq, "nickname": _member_nick(m), "role": str(m.get("role") or "")})

    warning = []
    if not valid:
        warning.append(f"群 {group_id} 拉不到合法成员（共 {len(members)} 条原始数据），已停止")
        return {"total": 0, "added": 0, "existed": 0, "skipped": 0, "warning": "; ".join(warning)}

    # 1) 上报群信息（群主/管理员从成员 role 推导）
    if on_progress:
        on_progress("上报群信息…")
    group = group_provider(group_id) if group_provider else {}
    owner = str(group.get("owner_qq") or "").strip()
    admins = str(group.get("admin_qqs") or "").strip()
    if not owner:
        owner = next((m["qq"] for m in valid if m["role"] == "owner"), "")
    if not admins:
        admins = ",".join(m["qq"] for m in valid if m["role"] in ("admin", "owner"))
    try:
        client.upsert_group(group_id, group_name=str(group.get("group_name") or ""),
                            owner_qq=owner, admin_qqs=admins, member_count=len(valid))
    except BackendError as e:
        warning.append(f"上报群信息失败：{e}")

    # 2) 批量查积分，区分已建档/未建档
    if on_progress:
        on_progress(f"查询 {len(valid)} 个成员积分…")
    registered: set[str] = set()
    qqs = [m["qq"] for m in valid]
    for i in range(0, len(qqs), QUERY_CHUNK):
        chunk = qqs[i:i + QUERY_CHUNK]
        try:
            rows = client.query_points_batch(chunk)
        except BackendError as e:
            warning.append(f"查询积分失败（{chunk[0]} 等 {len(chunk)} 个）：{e}")
            rows = []
        registered.update(str(r.get("qq")) for r in rows if r.get("exists"))

    # 3) 未建档的批量注册
    to_register = [{"qq": m["qq"], "nickname": m["nickname"], "group_id": group_id}
                   for m in valid if m["qq"] not in registered]
    added = existed = skipped = 0
    if on_progress:
        on_progress(f"注册 {len(to_register)} 个未建档会员…")
    for i in range(0, len(to_register), REGISTER_CHUNK):
        chunk = to_register[i:i + REGISTER_CHUNK]
        try:
            r = client.register_members_batch(chunk)
            added += int(r.get("added") or 0)
            existed += int(r.get("existed") or 0)
            skipped += int(r.get("skipped") or 0)
        except BackendError as e:
            warning.append(f"批量注册失败（{chunk[0].get('qq')} 等 {len(chunk)} 个）：{e}")

    if on_progress:
        on_progress("同步完成")
    return {"total": len(valid), "added": added, "existed": existed, "skipped": skipped,
            "warning": "; ".join(warning)}
