"""会员同步：把「会员群」全部成员注册到总后台会员库并拉取积分。

流程（幂等，可反复执行）：
1. 封禁群守卫：总后台里该群已存在且 status=banned → 停同步（停玩停同步）
2. 取群成员列表（provider 注入，GUI 里接 PluginClient）
3. 上报群信息（群名/群主/管理员/人数/群创建时间）→ /api/open/groups upsert
   （携带 executor_token：总后台校验执行器未被封禁，并把群绑定到该执行器）
4. 批量查积分（500/片）→ 已建档的跳过
5. 未建档成员批量注册（500/片）→ /api/open/members/batch（携带 token，注册人=操作员QQ）
6. 返回统计 {total, added, existed, skipped, warning}

异常约定：
- 执行器被封禁 / 操作员停用（ExecutorBanned/OperatorDisabled）直接上抛 —— 调用方
  （SyncWorker / GUI）按「停摆」处理，不停在 warn 里继续打无用的请求
- 其它单阶段失败记 warning 继续（网络抖动等可重试）

本模块不 import GUI/插件，只认 duck-typed provider：
- members_provider(group_id) -> [{qq, nickname?, role?}...]  （role 含 owner/admin/member）
- group_provider(group_id)   -> {group_name?, owner_qq?, admin_qqs?, group_create_time?}
  （可为 None 跳过；group_create_time 为 unix 秒或 'yyyy-MM-dd HH:mm:ss'，插件 /members 透传）
"""
from __future__ import annotations

from datetime import datetime

from .backend_client import BackendError, ExecutorBanned, OperatorDisabled, HbjfClient

QUERY_CHUNK = 500   # 批量查积分上限
REGISTER_CHUNK = 500  # 批量注册上限


def _member_qq(m: dict) -> str:
    """兼容 qq / user_id / uin 三种字段名的 QQ 提取，非法返回 ''。"""
    v = str(m.get("qq") or m.get("user_id") or m.get("uin") or "").strip()
    return v if v.isdigit() else ""


def _member_nick(m: dict) -> str:
    return str(m.get("nickname") or m.get("card") or "").strip()


def _ts_to_str(ts: float) -> str:
    """unix 时间戳 → yyyy-MM-dd HH:mm:ss（本机时区）；兼容毫秒；非法返回 ''。"""
    if ts > 1e12:
        ts /= 1000  # 毫秒
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return ""


def group_create_time_str(group: dict | None) -> str:
    """从群信息 payload 提取创建时间并格式化为 yyyy-MM-dd HH:mm:ss（本机时区）。
    支持 unix 秒/毫秒（插件 /members 透传）或已是该格式的字符串；取不到返回 ''。"""
    if not group:
        return ""
    v = group.get("group_create_time") or group.get("create_time") or ""
    if not v:
        return ""
    if isinstance(v, (int, float)):
        return _ts_to_str(float(v))
    s = str(v).strip()
    if len(s) == 19:
        return s
    try:
        return _ts_to_str(float(s))
    except ValueError:
        return ""


def run_member_sync(client: HbjfClient, group_id: str, members_provider, group_provider=None,
                    on_progress=None) -> dict:
    """同步一个群的成员为会员。on_progress(msg: str) 可选回调（每阶段调一次）。
    群被封禁 → 直接返回 warning（同步被叫停）；执行器被封禁 → 抛 ExecutorBanned。"""
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

    # 1) 封禁群守卫：总后台已把该群封禁 → 停玩停同步（未建档的群不拦，首报允许）
    if on_progress:
        on_progress("检查群状态…")
    try:
        banned = False
        for g in client.list_groups():
            if str(g.get("group_id")) == str(group_id):
                banned = str(g.get("status") or "active") == "banned"
                break
        if banned:
            warning.append(f"群 {group_id} 已被总后台封禁（停玩停同步），本次同步取消")
            return {"total": 0, "added": 0, "existed": 0, "skipped": 0, "warning": "; ".join(warning)}
    except (ExecutorBanned, OperatorDisabled):
        raise
    except BackendError as e:
        warning.append(f"检查群状态失败：{e}")  # 网络等瞬时问题不阻断后续

    # 2) 上报群信息（群主/管理员从成员 role 推导）
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
                            owner_qq=owner, admin_qqs=admins, member_count=len(valid),
                            create_time=group_create_time_str(group))
    except (ExecutorBanned, OperatorDisabled):
        raise
    except BackendError as e:
        warning.append(f"上报群信息失败：{e}")

    # 3) 批量查积分，区分已建档/未建档
    if on_progress:
        on_progress(f"查询 {len(valid)} 个成员积分…")
    registered: set[str] = set()
    qqs = [m["qq"] for m in valid]
    for i in range(0, len(qqs), QUERY_CHUNK):
        chunk = qqs[i:i + QUERY_CHUNK]
        try:
            rows = client.query_points_batch(chunk)
        except (ExecutorBanned, OperatorDisabled):
            raise
        except BackendError as e:
            warning.append(f"查询积分失败（{chunk[0]} 等 {len(chunk)} 个）：{e}")
            rows = []
        registered.update(str(r.get("qq")) for r in rows if r.get("exists"))

    # 4) 未建档的批量注册
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
        except (ExecutorBanned, OperatorDisabled):
            raise
        except BackendError as e:
            warning.append(f"批量注册失败（{chunk[0].get('qq')} 等 {len(chunk)} 个）：{e}")

    if on_progress:
        on_progress("同步完成")
    return {"total": len(valid), "added": added, "existed": existed, "skipped": skipped,
            "warning": "; ".join(warning)}
