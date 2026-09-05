"""总后台（hongbaojifen）开放接口客户端。

协议约定（与后端 /api/open/** 一致）：
- 所有请求带请求头 X-Api-Key（总后台管理员配发的对接密钥）
- 执行器身份用 token（总后台新增执行器时生成，一机一号），写操作建议都带
- 响应统一 {code:0, data:..., message:...}；code!=0 或 HTTP 非 2xx 抛 BackendError
- 业务错误码 → 专用异常子类：40310 执行器被封禁（ExecutorBanned，触发停摆）、
  40311 绑定操作员QQ被停用（OperatorDisabled）——调用方（GUI）依此展示封禁/停用态
- 命令通道：poll 拉取待执行命令（原子标记 sent，5 分钟未回报自动重投），
  执行后 report done/failed
"""
from __future__ import annotations

import requests

X_API_KEY_HEADER = "X-Api-Key"

# 总后台 ErrorCode 数值（见后端 ErrorCode 枚举）
CODE_EXECUTOR_BANNED = 40310
CODE_OPERATOR_DISABLED = 40311
CODE_PERMISSION_DENIED = 40300


class BackendError(Exception):
    """对接总后台失败（网络不通 / 非 2xx / code!=0）。message 已含可读中文描述。"""


class ExecutorBanned(BackendError):
    """执行器已被总后台封禁（40310）。收到后应立即停摆：停心跳/停玩法/停同步，
    等待总后台解封或更换 token。"""


class OperatorDisabled(BackendError):
    """绑定操作员QQ（admin_qq）已被总后台停用（40311）。"""


class HbjfClient:
    def __init__(self, base_url: str, api_key: str, token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.token = (token or "").strip()
        self._session = requests.Session()
        self._session.headers.update({X_API_KEY_HEADER: self.api_key, "User-Agent": "hbjf-agent/1.1"})

    # ---------- 底层请求 ----------

    def _post(self, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        return self._request("POST", path, json=body, timeout=timeout)

    def _get(self, path: str, params: dict | None = None, timeout: int = 30) -> dict:
        return self._request("GET", path, params=params, timeout=timeout)

    def _delete(self, path: str, timeout: int = 30) -> dict:
        return self._request("DELETE", path, timeout=timeout)

    def _request(self, method: str, path: str, **kw) -> dict:
        url = self.base_url + path
        try:
            resp = self._session.request(method, url, **kw)
        except requests.RequestException as e:
            raise BackendError(f"无法连接总后台（{self.base_url}）：{e}") from e
        try:
            payload = resp.json()
        except ValueError as e:
            raise BackendError(f"总后台返回非 JSON（HTTP {resp.status_code}）：{resp.text[:200]}") from e
        code = payload.get("code")
        if resp.status_code >= 400 or code != 0:
            msg = payload.get("message") or payload.get("error") or f"HTTP {resp.status_code}"
            exc_cls = _banned_error_type(code)
            if exc_cls is not None:
                raise exc_cls(f"总后台拒绝请求：{msg}")
            raise BackendError(f"总后台拒绝请求：{msg}")
        return payload.get("data") or {}

    # ---------- 执行器心跳 ----------

    def heartbeat(self, host: str = "", version: str = "", admin_qq: str = "",
                  admin_nickname: str = "", game_fee_rate: int | None = None) -> dict:
        """注册/续活执行器；顺带上报本机登录的管理员QQ（admin_qq 非空时总后台自动注册为操作员）。
        token 错或不存在抛 BackendError；已封禁抛 ExecutorBanned。返回 {name, server_time, online, admin_qq}。"""
        body = {"token": self.token, "host": host or "", "version": version or ""}
        if admin_qq:
            body["admin_qq"] = str(admin_qq).strip()
            if admin_nickname:
                body["admin_nickname"] = str(admin_nickname).strip()
        if game_fee_rate is not None:
            body["game_fee_rate"] = str(game_fee_rate)
        return self._post("/api/open/executor/heartbeat", body)

    # ---------- 命令通道 ----------

    def poll_commands(self) -> list[dict]:
        """拉取待执行命令（返回元素含 id/command/params/created_at，可能为空列表）。"""
        data = self._post("/api/open/executor/commands/poll", {"token": self.token})
        return data.get("commands") or []

    def report_command_result(self, command_id: int | str, status: str, message: str = "") -> dict:
        """回报命令执行结果，status ∈ {done, failed}。"""
        return self._post(f"/api/open/executor/commands/{command_id}/result",
                          {"token": self.token, "status": status, "message": message or ""})

    # ---------- QQ 群 ----------

    def upsert_group(self, group_id: str, group_name: str = "", owner_qq: str = "",
                     admin_qqs: str = "", member_count: str | int = "",
                     create_time: str = "", executor_token: str | None = None) -> dict:
        """上报群信息（幂等；只覆盖本次上报的非空字段）。返回含 created 布尔。
        create_time=群创建时间（yyyy-MM-dd HH:mm:ss，总后台严格校验格式，非空且格式错会忽略）；
        executor_token 默认取客户端 token —— 总后台据此校验执行器未被封禁并把群绑定到该执行器。"""
        body = {"group_id": str(group_id), "group_name": group_name or "",
                "owner_qq": owner_qq or "", "admin_qqs": admin_qqs or "",
                "executor_token": str(executor_token if executor_token is not None else self.token)}
        if member_count not in ("", None):
            body["member_count"] = str(member_count)
        if create_time and len(create_time) == 19:
            body["create_time"] = create_time
        return self._post("/api/open/groups", body)

    def list_groups(self, keyword: str = "", status: str = "") -> list[dict]:
        """总后台 QQ 群列表（元素含 group_id/group_name/owner_qq/admin_qqs/member_count/
        create_time/status/executor_id...）。status=banned 可筛封禁群。"""
        params = {}
        if keyword:
            params["keyword"] = keyword
        if status:
            params["status"] = status
        return self._get_result_list("/api/open/groups", params)

    # ---------- QQ 号管理（操作员，type 固定 admin_qq） ----------

    def upsert_qq_account(self, qq: str, nickname: str = "", remark: str = "") -> dict:
        """上报/登记操作员QQ（总后台 qq_accounts type=admin_qq，幂等）。"""
        body = {"qq": str(qq), "nickname": nickname or "", "remark": remark or ""}
        return self._post("/api/open/qq-accounts", body)

    def list_qq_accounts(self) -> list[dict]:
        """操作员QQ列表（元素含 qq/nickname/status/can_manual_points/last_login_at/...）。"""
        return self._get_result_list("/api/open/qq-accounts")

    def delete_qq_account(self, qq: str) -> dict:
        """删除操作员QQ（不存在抛 BackendError）。"""
        return self._delete(f"/api/open/qq-accounts/{qq}")

    # ---------- 会员 ----------

    def register_members_batch(self, members: list[dict], executor_token: str | None = None) -> dict:
        """批量注册会员（幂等 ≤1000/次）。members 元素: {qq, nickname?, group_id?}。
        返回 {added, existed, skipped}；executor_token 默认取客户端 token —— 总后台校验执行器
        未被封禁，并把其绑定操作员QQ记为注册人 registrar_qq。"""
        body = {"members": members,
                "executor_token": str(executor_token if executor_token is not None else self.token)}
        return self._post("/api/open/members/batch", body)

    def query_points_batch(self, qqs: list[str]) -> list[dict]:
        """批量查积分（≤500/次）。返回与传入顺序一致，元素 {qq, points, exists, ...}。"""
        data = self._get("/api/open/points/batch", params={"qqs": ",".join(qqs)})
        return data.get("list") or []

    def up_points(self, qq: str, points: int, reason: str = "", biz_no: str = "") -> dict:
        """上分（积分审批通过）。biz_no 幂等：同单号重复提交不会重复加。"""
        body = {"qq": str(qq), "points": int(points), "reason": reason or "",
                "executor_token": self.token}
        if biz_no:
            body["bizNo"] = biz_no
        return self._post("/api/open/points/up", body)

    def down_points(self, qq: str, points: int, reason: str = "", biz_no: str = "") -> dict:
        """下分（积分审批通过；余额不足会拒绝）。"""
        body = {"qq": str(qq), "points": int(points), "reason": reason or "",
                "executor_token": self.token}
        if biz_no:
            body["bizNo"] = biz_no
        return self._post("/api/open/points/down", body)

    # ---------- 游戏对局上报 ----------

    def report_game_round(self, round_id: str, play_name: str, group_id: str,
                          events: list[dict], play_id: int | str = "",
                          executor_token: str | None = None) -> dict:
        """上报一局游戏结算（总后台校验后逐条入账；round_id 幂等，重复上报返回 duplicate）。
        events 元素: {qq, nickname?, msg?, reply?, delta}；delta=该玩家本事件的积分变动。
        返回 {round_id, duplicate, member_count, total_delta, event_count, warning_count, warning}；
        warning 非空表示部分事件未入账（未知会员/会员停用/余额不足），回放里照记。"""
        body = {
            "executor_token": str(executor_token if executor_token is not None else self.token),
            "round_id": str(round_id)[:96],
            "group_id": str(group_id),
            "play_name": str(play_name or "")[:128],
            "events": events,
        }
        if play_id not in ("", None):
            body["play_id"] = str(play_id)
        return self._post("/api/open/games/report", body, timeout=60)

    def _get_result_list(self, path: str, params: dict | None = None) -> list[dict]:
        data = self._get(path, params)
        return data if isinstance(data, list) else data.get("list") or []

    # ---------- 玩法 ----------

    def list_rules(self) -> list[dict]:
        """总后台启用的玩法列表（元素含 id/name/description/version/status...，仅 status=active）。"""
        data = self._get("/api/open/rules")
        return data if isinstance(data, list) else data.get("list") or []

    def download_rule(self, rule_id: int | str, dest: str) -> str:
        """下载玩法文件到本地 dest 路径（返回 dest）。文件字节流，不走 JSON 解析；
        HTTP 4xx/5xx（如玩法已停用/不存在）抛 BackendError。"""
        url = self.base_url + f"/api/open/rules/{rule_id}/download"
        try:
            resp = self._session.get(url, timeout=60)
        except requests.RequestException as e:
            raise BackendError(f"无法连接总后台（{self.base_url}）：{e}") from e
        if resp.status_code >= 400:
            msg = None
            try:
                msg = resp.json().get("message")
            except ValueError:
                pass
            raise BackendError(f"下载玩法失败：{msg or f'HTTP {resp.status_code}'}")
        with open(dest, "wb") as fh:
            fh.write(resp.content)
        return dest


def _banned_error_type(code) -> type | None:
    """业务错误码 → 需要 GUI 特殊展示（封禁/停用）的异常类型；其余返回 None 走普通错误。"""
    try:
        num = int(code)
    except (TypeError, ValueError):
        return None
    if num == CODE_EXECUTOR_BANNED:
        return ExecutorBanned
    if num == CODE_OPERATOR_DISABLED:
        return OperatorDisabled
    return None
