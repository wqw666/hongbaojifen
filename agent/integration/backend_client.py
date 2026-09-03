"""总后台（hongbaojifen）开放接口客户端。

协议约定（与后端 /api/open/** 一致）：
- 所有请求带请求头 X-Api-Key（总后台管理员配发的对接密钥）
- 执行器身份用 token（总后台新增执行器时生成，一机一号）
- 响应统一 {code:0, data:..., message:...}；code!=0 或 HTTP 非 2xx 抛 BackendError
- 命令通道：poll 拉取待执行命令（原子标记 sent，5 分钟未回报自动重投），
  执行后 report done/failed
"""
from __future__ import annotations

import requests

X_API_KEY_HEADER = "X-Api-Key"


class BackendError(Exception):
    """对接总后台失败（网络不通 / 非 2xx / code!=0）。message 已含可读中文描述。"""


class HbjfClient:
    def __init__(self, base_url: str, api_key: str, token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.token = (token or "").strip()
        self._session = requests.Session()
        self._session.headers.update({X_API_KEY_HEADER: self.api_key, "User-Agent": "hbjf-agent/1.0"})

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
        if resp.status_code >= 400 or payload.get("code") != 0:
            msg = payload.get("message") or payload.get("error") or f"HTTP {resp.status_code}"
            raise BackendError(f"总后台拒绝请求：{msg}")
        return payload.get("data") or {}

    # ---------- 执行器心跳 ----------

    def heartbeat(self, host: str = "", version: str = "", group_id: str = "") -> dict:
        """注册/续活执行器；token 错或不存在抛 BackendError。"""
        body = {"token": self.token, "host": host, "version": version, "group_id": group_id}
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
                     admin_qqs: str = "", member_count: str | int = "") -> dict:
        """上报群信息（幂等；只覆盖本次上报的非空字段）。返回含 created 布尔。"""
        body = {"group_id": str(group_id), "group_name": group_name or "",
                "owner_qq": owner_qq or "", "admin_qqs": admin_qqs or ""}
        if member_count not in ("", None):
            body["member_count"] = str(member_count)
        return self._post("/api/open/groups", body)

    def list_groups(self) -> list[dict]:
        """总后台 QQ 群列表（元素含 group_id/group_name/owner_qq/admin_qqs/member_count/status...）。"""
        return self._get_result_list("/api/open/groups")

    def _get_result_list(self, path: str) -> list[dict]:
        data = self._get(path)
        return data if isinstance(data, list) else data.get("list") or []

    # ---------- QQ 号管理 ----------

    def upsert_qq_account(self, qq: str, qq_type: str = "qq", nickname: str = "", remark: str = "") -> dict:
        """上报 QQ 号（type: admin_qq=机器人/管理员号, qq=普通号；幂等）。"""
        return self._post("/api/open/qq-accounts",
                          {"qq": str(qq), "type": qq_type, "nickname": nickname or "", "remark": remark or ""})

    def list_qq_accounts(self, qq_type: str = "qq") -> list[dict]:
        """QQ 号列表（元素含 qq/nickname/type/status/remark...）。"""
        return self._get_result_list(f"/api/open/qq-accounts?type={qq_type}")

    def delete_qq_account(self, qq: str) -> dict:
        """删除 QQ 号（不存在抛 BackendError）。"""
        return self._delete(f"/api/open/qq-accounts/{qq}")

    # ---------- 会员 ----------

    def register_members_batch(self, members: list[dict]) -> dict:
        """批量注册会员（幂等 ≤1000/次）。members 元素: {qq, nickname?, group_id?}。
        返回 {added, existed, skipped}。"""
        return self._post("/api/open/members/batch", {"members": members})

    def query_points_batch(self, qqs: list[str]) -> list[dict]:
        """批量查积分（≤500/次）。返回与传入顺序一致，元素 {qq, points, exists, ...}。"""
        data = self._get("/api/open/points/batch", params={"qqs": ",".join(qqs)})
        return data.get("list") or []
