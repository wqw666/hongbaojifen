"""执行器登录号登记：通过 agent 登录并连接总后台的 QQ 都是管理员 QQ。

用法：register_self_admin_qq(client, login_info_provider)
- login_info_provider: 无参可调用，返回含 uin/nickname 的字典（NapCat get_login_info 形态）
- 幂等：同一 QQ 重复上报只是更新昵称/备注
"""
from __future__ import annotations

from .backend_client import HbjfClient


def register_self_admin_qq(client: HbjfClient, login_info_provider, nickname: str = "", remark: str = "agent登录QQ") -> dict:
    """把当前登录的 QQ 上报到总后台 qq_accounts（操作员，type=admin_qq）。返回总后台响应 data。"""
    info = login_info_provider() or {}
    qq = str(info.get("uin") or info.get("user_id") or "").strip()
    if not qq or not qq.isdigit():
        raise ValueError(f"无法获取当前登录QQ号（登录信息: {info}）")
    if not nickname:
        # NapCat GetQQLoginInfo 的昵称键是 nick；兼容 nickname/card
        nickname = str(info.get("nick") or info.get("nickname") or info.get("card") or "")
    return client.upsert_qq_account(qq, nickname=nickname, remark=remark)
